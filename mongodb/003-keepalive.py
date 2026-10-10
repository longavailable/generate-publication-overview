# -*- coding: utf-8 -*-
"""
* Updated on 2026/10/10
* python3 + pymongo
**
* Keep the MongoDB Atlas free cluster awake.
*
* Atlas 会统计「是否有连接」，连续30 天零连接就把免费集群自动暂停，且暂停后拒绝一切
* 连接，只能去控制台手动 Resume。这里周期性建立一次真实连接并ping，把计时器一直重置。
*
* 复用 001/002 相同的 MONGODB_USERNAME / MONGODB_PASSWORD secret，不需要额外配置凭据。
"""

import os
import sys
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

MONGODB_USERNAME = os.environ.get('MONGODB_USERNAME')
MONGODB_PASSWORD = os.environ.get('MONGODB_PASSWORD')

if not MONGODB_USERNAME or not MONGODB_PASSWORD:
    print('ERROR: MONGODB_USERNAME / MONGODB_PASSWORD secret 未配置。')
    sys.exit(1)

MONGODB_CONNECTION_STRING = 'mongodb+srv://%s:%s@cluster0.yq0ng.mongodb.net/' % (
    MONGODB_USERNAME, MONGODB_PASSWORD)


def keepalive():
    started = datetime.now(timezone.utc)
    # 免费集群选节点可能偏慢，但这是保活任务，宁可快速失败也不要卡住整个 workflow
    client = MongoClient(
        MONGODB_CONNECTION_STRING,
        serverSelectionTimeoutMS=20000,
        connectTimeoutMS=20000,
    )
    try:
        client.admin.command('ping')
        # 顺手确认业务集合还在，ping 成功但库被清空的情况也能发现
        db = client['publication']
        doc_count = db['meixiuyu'].estimated_document_count()
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print('OK: cluster is awake. ping succeeded in %.1fs.' % elapsed)
        print('    publication.meixiuyu estimated documents: %d' % doc_count)
    finally:
        client.close()


if __name__ == '__main__':
    try:
        keepalive()
    except ServerSelectionTimeoutError:
        print('')
        print('=' * 62)
        print('连接超时：集群很可能已经被 Atlas 自动暂停，或被网络访问列表拦下。')
        print('')
        print('若是「已暂停」，恢复步骤（只能手动，脚本无权恢复）：')
        print('  1. 打开 https://cloud.mongodb.com')
        print('  2. 进入对应 Project -> Clusters')
        print('  3. 找到显示 Paused 的集群，点 Resume')
        print('  4. 等1~2 分钟后再手动跑一次本 workflow')
        print('')
        print('若是访问列表问题：GitHub 托管 runner 没有固定 IP，')
        print('需在 Atlas 里把 0.0.0.0/0 加入 IP 访问列表（注意这会放宽来源限制）。')
        print('=' * 62)
        sys.exit(2)
    except PyMongoError as error:
        print('ERROR: MongoDB 操作失败: %s' % error)
        sys.exit(1)

    print('Keepalive done.')