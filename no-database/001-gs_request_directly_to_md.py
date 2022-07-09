from lots.util import *
from scholarly import scholarly
from datetime import date
import pathlib
import re

proxy()

authorName = 'Meixiu Yu'
pubItems = ['title', 'pub_year', 'author', 'journal', 'volume', 'number', 'pages'] 
urls = ['pub_url', 'eprint_url']

currentDate = date.today().strftime('%b-%d-%Y')
temlateFilename = 'publication-template.md'

search_author = scholarly.search_author(authorName)
result_author = scholarly.fill(next(search_author))
for publication in result_author['publications']:
	pub = scholarly.fill(publication)
	
	filename = '%d-01-01-%s.md' % (pub['bib']['pub_year'], '-'.join(pub['bib']['title'].replace('/','').split()[:5]) )
	filename = pathlib.Path('publication') / filename
	
	if filename.is_file():
		# update existing md file
		with open(filename, 'r') as file:
			filedata = file.read()
		
		old_citedby = re.search('Cited by(.*?)\n', filedata).group(0)
		if pub['num_citations']:
			new_citedby = 'Cited by    | %d (Updated on %s)   |\n' % (pub['num_citations'], currentDate)
		else:
			new_citedby = 'Cited by    | /   |\n'
		filedata = filedata.replace(old_citedby, new_citedby)
		
		with open(filename,'w') as file:
			file.write(filedata)
	else:
		
		with open(temlateFilename, 'r') as file :
			filedata = file.read()

		# normal items
		for item in pubItems:
			if item in pub['bib']:
				filedata = filedata.replace('{%s}' % item, str(pub['bib'][item]))
			else:
				filedata = filedata.replace('{%s}' % item, '/')
		
		for url in urls:
			if url in pub:
				filedata = filedata.replace('{%s}' % url, pub[url])
			else:
				filedata = filedata.replace('<{%s}>' % url, '/')
		
		# abstract
		if 'abstract' in pub['bib']:	
			abstract = pub['bib']['abstract']
			if not isinstance(abstract,str):
				abstract = abstract.text
			excerpt = ' '.join(abstract.split()[:50]) + '...'
			filedata = filedata.replace('{abstract}', abstract)
			filedata = filedata.replace('{excerpt}', excerpt)
		
		if pub['num_citations']:
			new_citedby = '%d (Updated on %s)' % (pub['num_citations'], currentDate)
		else:
			new_citedby = '/'
		filedata = filedata.replace('{num_citations}', new_citedby)
		
		# Write the file out again
		with open(filename, 'w') as file:
			file.write(filedata)
print('Done.')