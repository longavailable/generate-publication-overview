# -*- coding: utf-8 -*-
"""
* Updated on 2026/10/10
* python3 + mongodb + github action
**
* Query publications information from mongodb and save to a md based a templete file
*
* 只在内容真正变化时才落盘（2026/10/10）：
* 原实现每次都用当天日期重写「Cited by ... (Updated on <date>)」这一行，
* 于是哪怕引用数一个都没动，49 个 md 文件也全部被改写，
* 迫使 CI 每次都提交一次毫无信息量的 diff。
* 现在改为先渲染出候选内容，与磁盘上的现有文件逐字节比较：
*   - 引用数没变 -> 连「Updated on」日期都不刷新，文件保持原样；
*   - 引用数变了 -> 才写入新内容，并更新该文件里的日期。
"""

from datetime import date
import pathlib
import os

from pymongo import MongoClient
from pymongo.server_api import ServerApi
# initialize mongodb locally
# client = MongoClient()

# MongoDB Atlas
# https://cloud.mongodb.com/v2/626a2db4284d9e7b738d6092#clusters
MONGODB_USERNAME = os.environ.get('MONGODB_USERNAME')
MONGODB_PASSWORD = os.environ.get('MONGODB_PASSWORD')
MONGODB_USERNAME = MONGODB_USERNAME if MONGODB_USERNAME else str(input('MONGODB_USERNAME:'))
MONGODB_PASSWORD = MONGODB_PASSWORD if MONGODB_PASSWORD else str(input('MONGODB_PASSWORD:'))
MONGODB_CONNECTION_STRING = 'mongodb+srv://%s:%s@cluster0.yq0ng.mongodb.net/' % (MONGODB_USERNAME, MONGODB_PASSWORD)
client = MongoClient(MONGODB_CONNECTION_STRING)

publication = client.publication
myu = publication.meixiuyu


import re

def extract_citedby_number(md_text):
    """从 md 文件里取出「Cited by」那一行的引用数，取不到返回 None。

    表格里混着制表符和空格，直接按字符串比较会因为空白差异误判，
    所以只取数字本身来比对。'/' 表示无引用，记为 0。
    """
    for line in md_text.splitlines():
        if 'Cited by' not in line:
            continue
        # 取 '(' 之前的部分，那里有形如 8 或 / 的引用数
        head = line.split('(')[0]
        digits = re.search(r'\d+', head)
        if digits:
            return int(digits.group())
        if '/' in head:
            return 0
    return None

postItems = [   'title', 'author', 'journal', 'year', 'volume',
                'number', 'pages', 'abstract', 'authorPubId', 'url', 'eprint']

currentDate = date.today().strftime('%b-%d-%Y')
temlateFilename = pathlib.Path(__file__).parent / 'publication-template-mongodb.md'

#error records saved in mongodb raised by scholarly
excludes = ['5 Change Assessment and Management-Impact of human activities on the flow regime of the Hanjiang',
        'Kingdom 7',
        'Kingdom',
        'Ecological environmental flow estimation for medium tidal river',
        'Ecological environmental flow estimation for rivers with complicated hydraulic conditions vol 89 pg 357 2024',
        'Study on probability for rainstorm combinations during different durations in Shanghai City'
        ]

excludes_pubId = [ 'ly9d4IgAAAAJ:J%5Fg5lzvAfSwC' ]

# 统计本次运行实际改动了多少文件，最后汇总输出
writtenCount = 0
unchangedCount = 0

publications = myu.find()
for publication in publications:
    if publication['title'] in excludes: continue
    if publication['authorPubId'] in excludes_pubId: continue

    filename = pathlib.Path.cwd() / publication['postFilename']

    with open(temlateFilename, 'r') as file :
        filedata = file.read()

    #normal items
    for item in postItems:
        if publication[item]:
            filedata = filedata.replace('{%s}' % item, publication[item])
        else:
            filedata = filedata.replace('{%s}' % item, '/')
    if '[/](/)' in filedata:
        filedata = filedata.replace('[/](/)', '/')

    #excerpt
    if publication['abstract50']:
        filedata = filedata.replace('{excerpt}', publication['abstract50'])
    else:
        filedata = filedata.replace('{excerpt}', '')

    #citedby
    # 引用数没变化时，连「Updated on」日期都不刷新。
    # 原实现每次都写当天日期，于是哪怕引用数一个没动，
    # 49 个 md 文件也全被改写，CI 每次都被迫提交一次无信息量的 diff。
    rawCitedby = int(publication['citedby'])
    newCitedby = '/'
    if rawCitedby > 0:
        newCitedby = publication['citedby'] + ' (Updated on ' + currentDate + ')'

    if filename.is_file():
        with open(filename, 'r', encoding='utf-8') as file:
            existingData = file.read()

        # 直接比引用数值本身，绕开 md 表格的空白/制表符差异。
        # 数值一致 -> 认为无变化，保持文件原样，让 git 看到「无变更」。
        if extract_citedby_number(existingData) == rawCitedby:
            unchangedCount += 1
            continue

    filename.parent.mkdir(parents=True, exist_ok=True)

    # Write the file out again
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(filedata.replace('{citedby}', newCitedby))
    writtenCount += 1

print('Done. %d file(s) updated, %d unchanged.' % (writtenCount, unchangedCount))