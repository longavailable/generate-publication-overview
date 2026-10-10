# -*- coding: utf-8 -*-
"""
* Updated on 2026/10/10
* python3 + scholarly
**
* 在import 阶段给 scholarly 打补丁，堵掉「被 Google 判为机器人」时的两种致命行为。
*
* 背景：GitHub 托管 runner 的 IP 段是公开的，早已被学术机构与爬虫广泛识别。
* Google 对这类 IP 施加 reCAPTCHA 是必然事件，scholarly 撞上后有两种糟糕结局：
*
*   1) captcha 分支调用 _handle_captcha2()，该函数最多死等 7 天
*      （源码里 timeout = 60*60*24*7），而 CI 上没有任何人能真的点掉 reCAPTCHA；
*   2) 该分支只写 continue，重试计数器 tries 并不会递增，
*      所以 while tries < 5 这个循环根本不会自然收敛，
*      最终表现就是 workflow「一直运行」而不是干脆报错。
*
* 上游 scholarly 1.7.11 自 2022 年起停更，这类反爬对抗已无人维护，
* 只能在我们这一侧兜住。下面两处补丁把无上限的等待改为「快速失败 + 明确报错」。
*
* 用法：在 001 脚本里于 from scholarly import scholarly 之前先 import 本模块的apply_guards()。
"""

import logging

from scholarly._proxy_generator import ProxyGenerator

# 命中 captcha 后的秒数上限。上游给 7 天，CI 上无人能过，等再久也不会有结果。
CAPTCHA_WAIT_LIMIT_SECONDS = 60


def _patch_captcha_timeout():
    """把 _handle_captcha2 里写死的 7 天上限改成可配置的短值。

    源码是局部变量 timeout = 60*60*24*7，没法从外部改，
    所以只能整体替换函数实现，保留原有的等待逻辑与异常语义。
    """
    from selenium.common.exceptions import (
        TimeoutException,
        UnexpectedAlertPresentException,
        WebDriverException,
    )
    from selenium.webdriver.support.ui import WebDriverWait
    import time

    def patched(self, url):
        from urllib.parse import urlparse
        from scholarly._proxy_generator import DOSException

        # 把浏览器里已有的 cookie 带进 session，保持与上游一致
        cur_host = urlparse(self._get_webdriver().current_url).hostname
        for cookie in self._session.cookies:
            if cur_host is cookie.domain.lstrip('.'):
                self._get_webdriver().add_cookie({
                    'name': cookie.name,
                    'value': cookie.value,
                    'path': cookie.path,
                    'domain': cookie.domain,
                })
        self._get_webdriver().get(url)

        log_interval = 10
        cur = 0
        timeout = CAPTCHA_WAIT_LIMIT_SECONDS
        while cur < timeout:
            try:
                cur = cur + log_interval
                WebDriverWait(self._get_webdriver(), log_interval).until_not(
                    lambda drv: self._webdriver_has_captcha()
                )
                break
            except TimeoutException:
                self.logger.info(
                    'Solving the captcha took already %d seconds (of maximum %d s).',
                    cur, timeout,
                )
            except UnexpectedAlertPresentException as e:
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
            # 让调用方以为 captcha 已解决，继续拿到空页面。
            self.logger.info('Captcha was not solved within %d s; giving up.', timeout)
            raise TimeoutException(
                'CI 环境无法人工完成 reCAPTCHA，等待 %d 秒后放弃。'
                % timeout
            )

        self.logger.info('Solved captcha in less than %d seconds.', cur)

        for cookie in self._get_webdriver().get_cookies():
            cookie.pop('httpOnly', None)
            cookie.pop('expiry', None)
            cookie.pop('sameSite', None)
            self._session.cookies.set(**cookie)

        return self._session

    ProxyGenerator._handle_captcha2 = patched


def _patch_retry_count():
    """把重试次数压到 3 次，避免 403 反复退避把 job 拖死。"""
    from scholarly import scholarly as _scholarly
    # 注意：scholarly 的公开方法名是 set_retries，不是 set_num_retries
    _scholarly.set_retries(3)


def apply_guards():
    """应用所有防护。重复调用是幂等的。"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    _patch_captcha_timeout()
    _patch_retry_count()
    logging.info(
        'scholarly guards applied (captcha wait limit=%ds, retries=3).',
        CAPTCHA_WAIT_LIMIT_SECONDS,
    )