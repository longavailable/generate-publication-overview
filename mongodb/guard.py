# -*- coding: utf-8 -*-
"""
* Updated on 2026/10/10
* python3 + scholarly
**
* 在 import 阶段给scholarly 打补丁，修掉三类会拖垮采集的问题。
*
* 背景：Google Scholar 的反爬（403 /429 / reCAPTCHA）对数据中心 IP 一视同仁，
* 本机也一样被拦。scholarly 1.7.11 自 2022 年停更，反爬对抗已无人维护，
* 只能在我们这一侧兜住。
*
* 补丁一：captcha 死等
*   _handle_captcha2() 里timeout 写死为 60*60*24*7（7 天），
*   而自动化环境里没有人能真的点掉 reCAPTCHA，等下去毫无意义。
*   改为有限等待，超时抛 TimeoutException，而不是「莫名成功」地返回 session。
*
* 补丁二：重试次数
*   默认 5 次 403 退避会把 job 拖很久，压到 3 次。
*
* 补丁三：client has been closed
*   _navigator._get_page() 在函数开头把 client 存进局部变量 session，
*   但 403 分支里的 self._new_session() 会 close() 掉这个 client
*   并新建一个赋给 self._session1。局部变量不会跟着更新，
*   下一轮循环仍拿着已关闭的对象发请求，于是抛
*   RuntimeError: Cannot send a request, as the client has been closed.
*   表现为每处理一篇论文就先报一次错再重试成功——数据没丢，
*   但每次白扔一个请求并多耗一轮重试计数（86 篇约多花 2 分钟）。
*
*   修法：每轮循环开头从 pm._session 重新取一次，让局部变量始终指向
*   当前活着的那个 client。
*   之所以不 monkey patch httpx.Client.close()「让它别真关」：
*   那是全局改动，会让所有 client（包括与 scholarly 无关的）失去
*   正常的关闭语义，且依赖 _transport._pool 私有属性；
*   局部重读引用既解决陈旧问题，旧 client 也能被正常关闭、连接正常回收。
*
* 用法：在 001 脚本里于 from scholarly import scholarly 之前调用 apply_guards()。
"""

import logging
import random
import time

from httpx import TimeoutException
from requests.exceptions import Timeout

from scholarly._navigator import Navigator
from scholarly._proxy_generator import (
    DOSException,
    MaxTriesExceededException,
    ProxyGenerator,
)
from scholarly.data_types import ProxyMode

# 命中 captcha 后的秒数上限。上游给 7 天，自动化环境无人能过，等再久也不会有结果。
CAPTCHA_WAIT_LIMIT_SECONDS = 60

# 重试次数：上游默认 5 次，403 每次退避 60~120 秒，5 次足够把 job 拖死。
MAX_RETRIES = 3


def _patch_captcha_timeout():
    """把 _handle_captcha2 里写死的 7 天上限改成可配置的短值。

    源码是局部变量 timeout = 60*60*24*7，没法从外部改，
    所以只能整体替换函数实现，保留原有的等待逻辑与异常语义。
    """
    from selenium.common.exceptions import (
        TimeoutException as SeleniumTimeoutException,
        UnexpectedAlertPresentException,
        WebDriverException,
    )
    from selenium.webdriver.support.ui import WebDriverWait
    from urllib.parse import urlparse

    def patched(self, url):
        from urllib.parse import urlparse
        from scholarly._proxy_generator import DOSException

        # 上游此处直接 self._get_webdriver().current_url，而 _get_webdriver()
        # 找不到浏览器时返回 None，于是抛
        # AttributeError: 'NoneType' object has no attribute 'current_url'，
        # 异常信息完全看不出「其实是没装 Chrome/Firefox」，
        # 还会被 _get_page 的宽 except 吞掉、误判成网络故障继续重试。
        driver = self._get_webdriver()
        if driver is None:
            self.logger.info(
                'Captcha hit, but no Chrome/Firefox driver is available; '
                'cannot be solved unattended.')
            raise DOSException(
                'reCAPTCHA 需要浏览器人工处理，当前环境没有可用浏览器。')

        # 把浏览器里已有的 cookie 带进 session，保持与上游一致
        cur_host = urlparse(driver.current_url).hostname
        for cookie in self._session.cookies:
            # 只设置匹配当前域的 cookie，跨域 cookie 会让 webdriver 报错
            if cur_host is cookie.domain.lstrip('.'):
                driver.add_cookie({
                    'name': cookie.name,
                    'value': cookie.value,
                    'path': cookie.path,
                    'domain': cookie.domain,
                })
        driver.get(url)

        log_interval = 10
        cur = 0
        timeout = CAPTCHA_WAIT_LIMIT_SECONDS
        while cur < timeout:
            try:
                cur = cur + log_interval
                WebDriverWait(driver, log_interval).until_not(
                    lambda drv: self._webdriver_has_captcha()
                )
                break
            except SeleniumTimeoutException:
                self.logger.info(
                    'Solving the captcha took already %d seconds (of maximum %d s).',
                    cur, timeout,
                )
            except UnexpectedAlertPresentException as e:
                # reCAPTCHA 偶发「Cannot contact reCAPTCHA」，重试即可
                self.logger.info('Unexpected alert while waiting for captcha completion: %s', e.args)
                time.sleep(15)
            except DOSException as e:
                self.logger.info('Google thinks we are DOSing the captcha.')
                raise e
            except WebDriverException as e:
                self.logger.info('Browser seems to be disfunctional - closed by user?')
                raise e
            except Exception as e:
                self.logger.info(
                    'Unhandled %s while waiting for captcha completion: %s',
                    type(e).__name__, e.args,
                )
        else:
            # 这里必须抛出，否则会「莫名成功」地返回 session，
            # 让调用方以为 captcha 已解决，继续解析一个空页面。
            self.logger.info('Captcha was not solved within %d s; giving up.', timeout)
            raise SeleniumTimeoutException(
                'reCAPTCHA 无法在无人值守环境下人工完成，等待 %d 秒后放弃。' % timeout
            )

        self.logger.info('Solved captcha in less than %d seconds.', cur)

        for cookie in driver.get_cookies():
            cookie.pop('httpOnly', None)
            cookie.pop('expiry', None)
            cookie.pop('sameSite', None)
            self._session.cookies.set(**cookie)

        return self._session

    ProxyGenerator._handle_captcha2 = patched


def _patch_retry_count():
    """把重试次数压到可控范围，避免 403 退避把 job 拖死。"""
    from scholarly import scholarly as _scholarly
    # 注意：scholarly 的公开方法名是 set_retries，不是 set_num_retries
    _scholarly.set_retries(MAX_RETRIES)


def _patch_stale_session_reference():
    """消除「Cannot send a request, as the client has been closed.」。

    与上游 _get_page 的唯一区别：每轮循环开头重新从 pm._session 取 client。
    上游只在函数入口取一次，中途 403 分支 self._new_session() 换掉 client 后
    局部变量仍指向旧的、已关闭的那个，下一轮必然抛 RuntimeError。
    其余分支逻辑（200 / 404 / captcha / 403 / 302 跳转 /退避 / 换代理）
    与上游逐行一致，避免引入额外行为差异。
    """
    original_get_page = Navigator._get_page

    def patched_get_page(self, pagerequest: str, premium: bool = False) -> str:
        self.logger.info('Getting %s', pagerequest)
        tries = 0
        if ('citations?' in pagerequest) and (not premium):
            pm = self.pm2
            session = self._session2
            premium = False
        else:
            pm = self.pm1
            session = self._session1
            premium = True
        if pm.proxy_mode is ProxyMode.SCRAPERAPI:
            self.set_timeout(60)
        timeout = self._TIMEOUT

        while tries < self._max_retries:
            try:
                # 关键修复：以上一条重试路径可能已换掉 client，这里始终取活的
                session = pm._session
                w = random.uniform(1, 2)
                time.sleep(w)
                resp = session.get(pagerequest, timeout=timeout)
                if premium is False:  # premium methods may contain sensitive information
                    self.logger.debug('Session proxy config is {}'.format(pm._proxies))

                has_captcha = self._requests_has_captcha(resp.text)

                if resp.status_code == 200 and not has_captcha:
                    return resp.text
                elif resp.status_code == 404:
                    # scholar_id 若是估算值，会先404/302 再跳到正确主页，
                    # 此时必须用同一个 session 再试，否则永远跳不对。
                    self.logger.debug('Got a 404 error. Attempting with same proxy')
                    tries += 1
                    continue
                elif has_captcha:
                    self.logger.info('Got a captcha request.')
                    session = pm._handle_captcha2(pagerequest)
                    continue
                elif resp.status_code == 403:
                    self.logger.info('Got an access denied error (403).')
                    if not pm.has_proxy():
                        self.logger.info('No other connections possible.')
                        if not self.got_403:
                            self.logger.info('Retrying immediately with another session.')
                        else:
                            if pm.proxy_mode not in (ProxyMode.LUMINATI, ProxyMode.SCRAPERAPI):
                                w = random.uniform(60, 2 * 60)
                                self.logger.info(
                                    'Will retry after %.2f seconds (with another session).', w)
                                time.sleep(w)
                        self._new_session(premium=premium)
                        self.got_403 = True
                        continue
                    else:
                        self.logger.info("We can use another connection... let's try that.")
                elif resp.status_code == 302 and resp.has_redirect_location:
                    self.logger.debug('Got a redirect.')
                    pagerequest = resp.headers['location']
                else:
                    self.logger.info(
                        'Response code %d. Retrying...', resp.status_code)

            except DOSException:
                if not pm.has_proxy():
                    self.logger.info('No other connections possible.')
                    w = random.uniform(60, 2 * 60)
                    self.logger.info(
                        'Will retry after %.2f seconds (with the same session).', w)
                    time.sleep(w)
                    continue
            except (Timeout, TimeoutException) as e:
                err = 'Timeout Exception %s while fetching page: %s' % (type(e).__name__, e.args)
                self.logger.info(err)
                if timeout < 3 * self._TIMEOUT:
                    self.logger.info('Increasing timeout and retrying within same session.')
                    timeout = timeout + self._TIMEOUT
                    continue
                self.logger.info('Giving up this session.')
            except Exception as e:
                err = 'Exception %s while fetching page: %s' % (type(e).__name__, e.args)
                self.logger.info(err)
                self.logger.info('Retrying with a new session.')

            tries += 1
            try:
                session, timeout = pm.get_next_proxy(
                    num_tries=tries, old_timeout=timeout,
                    old_proxy=pm._proxies.get('http', None))
            except Exception:
                self.logger.info('No other secondary connections possible. '
                                 'Using the primary proxy for all requests.')
                break

        # 次级代理走不通，退回主代理再试一次
        if not premium:
            return self._get_page(pagerequest, True)
        raise MaxTriesExceededException('Cannot Fetch from Google Scholar.')

    patched_get_page.__doc__ = original_get_page.__doc__
    Navigator._get_page = patched_get_page


def apply_guards():
    """应用所有防护。重复调用是幂等的。"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    _patch_captcha_timeout()
    _patch_retry_count()
    _patch_stale_session_reference()
    logging.info(
        'scholarly guards applied (captcha wait limit=%ds, retries=%d, stale-session fix on).',
        CAPTCHA_WAIT_LIMIT_SECONDS,
        MAX_RETRIES,
    )