# -*- coding: utf-8 -*-
"""
* Updated on 2025/07/11
* python3 + scholarly + mongodb + github action
**
* Request MYU's publications infomation from Google Scholar
* Save them into MongoDB
"""

from scholarly import scholarly
from scholarly import ProxyGenerator
import re
from datetime import date
from pprint import pprint
from tqdm import tqdm		# progress bar
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

fields = ['title', 'title5', 'year', 'postFilename', 'author', 'journal',  'volume', 
          'number', 'pages', 'abstract', 'abstract50', 'url', 'eprint', 'citedby', 'authorPubId']
tag = '_status'
fieldsTag = [ field + tag for field in fields]
document0 = {field: None for field in fields}
documentTag = {field: False for field in fieldsTag}
document0.update(documentTag)
documentAuxiliary = {
                    'overall': True,
                    'citedby_updated_on': date.today().strftime('%b-%d-%Y')
                    }
document0.update(documentAuxiliary)
'''
print(document0)
fields = fields + fieldsTag
pprint(fields)
pprint(document0)
'''

try:
    # search by author name, account login needed (2025/07/11)
    authorName = 'Meixiu Yu'
    search_author = scholarly.search_author(authorName)
    search_author = next(search_author)
except:
    # search by author id
    authorId = 'ly9d4IgAAAAJ'
    search_author = scholarly.search_author_id(authorId)
result_author = scholarly.fill(search_author)

'''
pprint(search_author)   #scholarly.pprint(search_author)
pprint(result_author['publications'])   #scholarly.pprint(result_author['publications'])
print(len(result_author['publications']))
'''

for publication in tqdm(result_author['publications']):
    document = document0.copy()		# initalize the dictionary
    pub = scholarly.fill(publication)
    '''
    print(document)
    print(pub)
    '''
    if not pub['bib']['title']: continue	# No title, no publication
    if len(re.compile(u'[^\u4e00-\u9fa5]').sub('',pub['bib']['title'])) > 0: continue	# Exclude papers in Chinese

    # extract infomation from Google Scholar
    for field in fields:
        # keywords was updated by scholarly
        field_tmp = field
        if field == 'year':
            field_tmp = 'pub_year'
        elif field == 'citedby':
            field_tmp = 'num_citations'
        elif field == 'url':
            field_tmp = 'pub_url'
        elif field == 'authorPubId':
            field_tmp = 'author_pub_id'
        else:
            field_tmp = field
        if field_tmp in pub['bib']:
            value = pub['bib'][field_tmp]
            if not isinstance(value, (str, float, int)):	# not an ideal result, html tag object mostly
                value = value.text
            document[field]=' '.join(str(value).split())	# make the string more neat
            document[field+tag] = True
        if field_tmp in pub:
            value = pub[field_tmp]
            if not isinstance(value, (str, float, int)):
                value = value.text
            document[field]=' '.join(str(value).split())
            document[field+tag] = True
        if field_tmp == 'title' and document[field]:
            document['title'] =  ' '.join(re.sub(r'[^A-za-z0-9 -]+','',document['title']).split())	# remove 'non-rgular' characters

    # 5-word title
    document['title5'] = '-'.join(document['title'].split()[:5])	# short title
    document['title5_status'] = True
    if document['year_status']:
        document['postFilename'] = 'publication/' + document['year'] + '-01-01-' + document['title5'] + '.md'
        document['postFilename_status'] = True
    if document['abstract_status']:
        document['abstract50'] = ' '.join(document['abstract'].split()[:50]) + '...'	# short abstract
        document['abstract50_status'] = True
    
    '''
    pprint(document)
    print(document['title5'])
    '''
    for field in fields:
        if not document[field+tag]:
            document['overall'] = False	# to evaluate quickly
            break
    document['citedby_status'] = False

    # create or update a document in the collection (meixiuyu) of database (publication)
    dataFilter = {	'title5': document['title5'],		# a filter, add more condition if needed.
                    'year': document['year']}
    #data = myu.find(dataFilter) datalen = len(list(data))
    datalen = myu.count_documents(dataFilter)

    print(datalen)
    if datalen == 0:	# no document, then create a new one
        pprint(document)
        myu.insert_one(document)
    elif datalen == 1:	# exits, then update something
        data = myu.find_one(dataFilter)	# find_one return a dictionary, while find return a cursor object
        if not data['overall'] or data['citedby_updated_on'] != document['citedby_updated_on']: # when overall is true, and date is today, update nothing
            for field in fields:
                if field == 'citedby':	# update citedby infomation
                    citedbyData = { '$set':
                                                        {	'citedby':document['citedby'],
                                                            'citedby_updated_on': document['citedby_updated_on']}
                                                }
                    myu.update_one(dataFilter, citedbyData)
                    print('%s of %s in %s was updated.' %(field, document['title5'], document['year']))
                elif not data[field+tag] and document[field+tag]:	# some tag in database is false and now we get a true data, then update it
                    data_to_update = 	{ '$set':
                                                            {	field: document[field],
                                                                field+tag: True}
                                                        }
                    myu.update_one(dataFilter, data_to_update)
                    print('%s of %s in %s was updated.' %(field, document['title5'], document['year']))
        else:
            print('%s in %s is the latest.' %(document['title5'], document['year']))
            
    else:	# more than one documents, print some reminder, delete repeated one or add more filter condition to 'dataFilter'.
        print('%s in %s was duplicated.' %(document['title5'], document['year']))
        
    document.clear()
print('Done.')
