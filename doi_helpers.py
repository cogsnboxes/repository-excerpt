import requests
import sys
import codecs
import base64
from django.shortcuts import render

"""
    Присвоение DOI выпуску:
    1. Создать redirect_item: ссылка ведет на истинную страницу выпуска, название редиректа - DOI с заменой '/' на '_'.
    2. Скопировать xml-файл другого выпуска, переименовать и изменить внутри.
    3. post_doi_metadata
    4. _mint_doi

"""

def _mint_doi(doi, url,debug=False):
    #print('_mint_doi start')
    if debug:
        print('_mint_doi(',doi,url,')')

    username = 'SPBPU.SFEDU'
    password = '<datacite password>'
    endpoint = 'https://mds.datacite.org/doi'
    data_str = 'doi='+doi+'\nurl='+url
    if debug:
        print('data=',data_str)
    response = requests.post(endpoint,auth = (username, password), data = data_str, headers = {'Content-Type':'text/plain;charset=UTF-8'})

    if debug:
        print('response status code=',response.status_code,'],text=[',response.text,']')

    #print('_mint_doi end:',response)
    return response

def _post_doi_metadata(doi, metadata_filename,debug=False):

    if debug:
        print('_post_doi_metadata(',doi,metadata_filename,')')

    username = 'SPBPU.SFEDU'
    password = '<datacite password>'
    endpoint = 'https://mds.datacite.org/metadata'
    if debug:
        print('endpoint=',endpoint)
    metadata_file = codecs.open(metadata_filename, 'r', encoding='utf-8').read().strip()
    response = requests.post(endpoint,auth = (username, password), data = metadata_file.encode('utf-8'), headers = {'Content-Type':'application/xml;charset=UTF-8'})
    if debug:
        print('response status code=[',response.status_code,'],type=',type(response.status_code),',text=[',response.text,']')
    return response

def _create_metadata_xml_file(data,doi,path,debug=False):
    if debug:
        print('_create_metadata_xml_file(',data,doi,path,')')
    """
    doi
    translated_title
    creators
    publisher
    publication_year
    resource_type
    keywords
    codes
    issue_year
    journal_issn
    abstract
    language_code
    grant
    """
    if debug:
        print('checking parameters, next line must be "parameters are ok", otherwise they are not ok')

    #checklist
    if 'doi' not in data: 
        return None
    if 'translated_title' not in data: 
        return None
    #if 'creators' not in data: return None
    if 'publisher' not in data: 
        return None
    if 'publication_year' not in data: 
        return None
    if 'resource_type' not in data: 
        return None
    #if 'keywords' not in data: return None
    #if 'codes' not in data: return None
    if 'issue_date' not in data: 
        return None
    if 'journal_issn' not in data and 'isbn' not in data: 
        return None
    #if 'abstract' not in data: return None
    if 'language_code' not in data: 
        return None
    #if 'grant' not in data: return None

    if debug:
        print('parameters are ok')

    response = render(None,"nexus/rpc_helper_datacite_doi_template.xml",context=data)
    if not response.status_code == 200:
        if debug:
            print("error rendering template:",response.status_code,response.reason_phrase)
        return None

    if debug:
        print('response status code is',response.status_code)

    content = response.content.decode('utf-8')
    filename = doi.split('/')[1]+'.xml'
    
    if debug:
        print('filename=',filename)
    with open(path+filename, 'w') as static_file:
        static_file.write(content)
    if debug:
        print('_create_metadata_xml_file done for',path+filename,',returning')
        
    return path+filename

def submit_doi_rest(doi,url,asset,debug=False):
    if debug:
        print('submit_doi_rest(',doi,',',url,',',str(asset),')')
    #1. create metadata xml file

    metadata_path = '/var/www/localhost/django/hub/media/doi_metadata/'
    filename = doi.split('/')[1]+'.xml'
    metadata_filename = metadata_path+filename
    if debug:
        print('metadata_filename=',metadata_filename)
    template_name='nexus/rpc_helper_datacite_doi_template_'+asset.type.sysname+'.xml'
    if debug:
        print('template_name=',template_name)
    metadata={'asset':asset}#asset.payload
    response = render(None,template_name,context=metadata)
    if not response.status_code == 200:
        if debug:
            print('ERROR rendering template')
        raise Exception('Error rendering template:'+template_name)
        
    content = response.content.decode('utf-8')
    with open(metadata_filename, 'w') as static_file:
        static_file.write(content)

    encodedBytes = base64.b64encode(content.encode("utf-8"))
    encoded_str = str(encodedBytes, "utf-8")
    payload = {
        "data": {
            "id": doi,
            "type": "dois",
            "attributes": {
                "event": "publish",
                "doi": doi,
                "url": url,
                "xml": encoded_str
            }
        }
    }
    username = 'SPBPU.SFEDU'
    password = '<datacite password>'
    endpoint = 'https://api.datacite.org/dois'
    if debug:
        print('endpoint=',endpoint)
    #metadata_file = codecs.open(metadata_filename, 'r', encoding='utf-8').read().strip()
    response = requests.post(
        endpoint,
        auth = (username, password), 
        json = payload, 
        headers = {'Content-Type': 'application/vnd.api+json'}
    )
    if debug:
        print('PAYLOAD:')
        print(payload)
        print('END OF PAYLOAD')
    if debug:
        print('response status code=[',response.status_code,'],type=',type(response.status_code),',text=[',response.text,']')
    return response


def submit_doi_new(doi,url,asset,debug=False):
    if debug:
        print('submit_doi_new(',doi,',',url,',',str(asset),')')
    #1. create metadata xml file

    metadata_path = '/var/www/localhost/django/hub/media/doi_metadata/'
    filename = doi.split('/')[1]+'.xml'
    metadata_filename = metadata_path+filename
    if debug:
        print('metadata_filename=',metadata_filename)
    template_name='nexus/rpc_helper_datacite_doi_template_'+asset.type.sysname+'.xml'
    if debug:
        print('template_name=',template_name)
    metadata={'asset':asset}#asset.payload
    response = render(None,template_name,context=metadata)
    if not response.status_code == 200:
        if debug:
            print('ERROR rendering template')
        raise Exception('Error rendering template:'+template_name)
        
    content = response.content.decode('utf-8')
    with open(metadata_filename, 'w') as static_file:
        static_file.write(content)

    #2. post doi metadata to DataCite endpoint
    if debug:
        print('_post_doi_metadata(',doi,',',metadata_filename,')')

    result = _post_doi_metadata(doi=doi,metadata_filename=metadata_filename,debug=debug)
    if not result.status_code in [200,201,]:
        if debug:
            print('ERROR posting DOI metadata:',result.status_code,result.text)
        raise Exception('Error posting metadata: status_code='+str(result.status_code)+", info: "+result.text)

    #3. mint DOI at DataCite endpoint
    if debug:
        print('_mint_doi(',doi,',',url,')')
    result = _mint_doi(doi=doi,url=url,debug=debug)
    if not result.status_code in [200,201,]:
        if debug:
            print('ERROR ninting DOI:',result.status_code,result.text)
        raise Exception('Error minting DOI: status_code='+str(result.status_code)+", info: "+result.text)
    if debug:
        print('done')
    return True
    
def submit_doi(doi, url, metadata,debug=False):
    debug=False
    if debug:
        print('submit_doi(',doi,url,'<metadata>)')
        print(metadata)
    metadata_filename = _create_metadata_xml_file(data=metadata,doi=doi,path='/var/www/localhost/django/hub/media/doi_metadata/',debug=debug)
    result = _post_doi_metadata(doi=doi,metadata_filename=metadata_filename,debug=debug)
    if not result.status_code in [200,201,]:
        if debug:
            print('_post_doi_metadata returned wrong status_code: it returned',result.status_code,'but it must be either 200 or 201, exiting submit_doi')
        return False
    else:
        if debug:
            print('_post_doi_metadata returned normal status_code')
        else:
            pass
    _url = url
    result = _mint_doi(doi=doi,url=_url,debug=debug)
    if not result.status_code in [200,201,]:
        if debug:
            print('_mint_doi returned wrong status_code: it returned',result.status_code,'but it must be either 200 or 201, exiting submit_doi')
        return False
    else:
        if debug:
            print('_mint_doi returned normal status_code')
            print('submit_doi successfully returned')

    return True