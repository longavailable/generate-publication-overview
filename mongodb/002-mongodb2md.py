# -*- coding: utf-8 -*-
"""
* Updated on 2025/07/11
* python3 + mongodb + github action
**
* Query publications information from mongodb and save to a md based a templete file
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
MONGODB_CONNECTION_STRING = 'mongodb://%s:%s@cluster0-shard-00-00.yq0ng.mongodb.net:27017,cluster0-shard-00-01.yq0ng.mongodb.net:27017,cluster0-shard-00-02.yq0ng.mongodb.net:27017/publication?ssl=true&replicaSet=atlas-k84fd7-shard-0&authSource=admin&retryWrites=true&w=majority' % (MONGODB_USERNAME, MONGODB_PASSWORD)
client = MongoClient(MONGODB_CONNECTION_STRING)

publication = client.publication
myu = publication.meixiuyu


postItems = [   'title', 'author', 'journal', 'year', 'volume',
                'number', 'pages',  'abstract', 'authorPubId', 'url', 'eprint']

currentDate = date.today().strftime('%b-%d-%Y')
temlateFilename = pathlib.Path(__file__).parent / 'publication-template-mongodb.md'

#error records saved in mongodb raised by scholarly
excludes = ['5 Change Assessment and Management-Impact of human activities on the flow regime of the Hanjiang',
        'Kingdom 7',
        'Kingdom',
        'Ecological environmental flow estimation for medium tidal river',
        ]

publications = myu.find()
for publication in publications:
    if publication['title'] in excludes: continue

    filename = pathlib.Path.cwd() / publication['postFilename']
    filename.parent.mkdir(parents=True, exist_ok=True)	

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
    if int(publication['citedby'])>0:
        filedata = filedata.replace('{citedby}',publication['citedby'] + ' (Updated on ' + currentDate + ')' )
    else:
        filedata = filedata.replace('{citedby}', '/')

    # Write the file out again
    with open(filename, 'w') as file:
        file.write(filedata)

print('Done.')
