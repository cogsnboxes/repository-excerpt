#-*- coding: utf-8 -*-

import uuid
import subprocess
import sys
import os
import shutil
import logging
import datetime
import threading
import requests

from django.http import HttpResponse
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from nexus.notifications import nexus_sendmail
from nexus.models import *

class AssetTypeChangeStation:
    """
        properties.asset_payload_field_name(str) contains payload key where the value used for assettype transformation is located, e.g. 'material_category' 
        properties.transformations(dict) contains assettype sysnames as keys and lists of asset.payload[asset_payload_field_name] values to transform,
        e.g. {'teaching_aid':['учебно-методическое пособие','учебно-методический материал']}
    """
    def perform(self,asset):
        from nexus.models import AssetType
        #print("AssetTypeChangeStation:",self)
        asset_payload_field_name = asset.stationinroute.station.properties['asset_payload_field_name']
        value = asset.payload.get(asset_payload_field_name,[None])[0]
        if not value:
            return
        transformations = asset.stationinroute.station.properties['transformations']
        for assettype_sysname in transformations:
            if value.strip().lower() in transformations[assettype_sysname]:
                new_asset_type = AssetType.objects.get(sysname=assettype_sysname)
                asset.type=new_asset_type
                asset.save()
                break

class DOIPacketMintingStationConference:
    def perform(self, asset):
        debug = False

        import uuid
        import datetime
        from nexus.models import Asset
        import requests

        #make xml, post to crossref, send to problem station if needed, send articles to doi minting,
        #'doi_minting_fail_description'
        if 'doi' in asset.payload: #don't assign doi more than once ever
            asset_history_record = dict()
            asset_history_record['datetime'] = str(datetime.datetime.now())
            asset_history_record['action'] = 'MINT DOI ABORT - ASSET ALREADY HAS IT'
            if asset.operator:
                asset_history_record['operator'] = asset.operator.pk
            else:
                asset_history_record['operator'] = 0

            asset_history_record['stationinroute'] = asset.stationinroute.pk
            asset.meta['history'].append(asset_history_record)
            asset.save()
            return

        try:
            journal = Asset.objects.filter(type__id=41).filter(payload__title__0=asset.payload['doi_allocation_quota_source'][0])[0]
        except:
            journal = None
        articles = Asset.objects.filter(type__id__in=[39,60]).filter(payload__journal_issue_for_doi__0 = str(asset.pk))

        dt=datetime.datetime.now()
        try:
            username = User.objects.get(pk=asset.meta['creator']).username
        except:
            username = "abogomolov"
        try:
            #==================================================================================================
            # MANDATORY HEAD SECTION
            #==================================================================================================
            
            xml_head = "<head>"
            xml_head += "<doi_batch_id>"+str(asset.pk)+"</doi_batch_id>"
            xml_head += "<timestamp>"+str(dt.year)+str(dt.month).rjust(2,'0')+str(dt.day).rjust(2,'0')+str(dt.hour).rjust(2,'0')+str(dt.minute).rjust(2,'0')+"</timestamp>"
            xml_head += "<depositor>"
            xml_head += "<depositor_name>sfedu</depositor_name>"

            #xml_head += "<email_address>"+username+"@sfedu.ru</email_address>"

            xml_head += "<email_address>abogomolov@sfedu.ru</email_address>"
                
            xml_head += "</depositor>"
            xml_head += "<registrant>Southern Federal University</registrant>"
            xml_head += "</head>"

            xml_data = "<body><conference>"
            xml_data += "<event_metadata>"
            xml_data += "<conference_name>" + journal.payload['title_en'][0] + "</conference_name>"
            xml_data += "</event_metadata>"
            xml_data += "<proceedings_metadata>"
            xml_data += "<proceedings_title>Proceedings of "+journal.payload['title_en'][0]+"</proceedings_title>"
            xml_data += "<publisher>"
            xml_data += "<publisher_name>Southern Federal University</publisher_name>"
            xml_data += "</publisher>"
            xml_data += "<publication_date media_type='online'>"
            xml_data += "<year>"+str(dt.year)+"</year>"
            xml_data += "</publication_date>"
            xml_data += "<noisbn reason='archive_volume'/>"
            if 'doi' in journal.payload and 'hyperlink' in journal.payload:
                xml_data += "<doi_data>"
                xml_data += "<doi>"+journal.payload['doi'][0].strip()+"</doi>"
                xml_data += "<resource>"+journal.payload['hyperlink'][0].strip()+"</resource>"
                xml_data += "</doi_data>"
            xml_data += "</proceedings_metadata>"
            """
            <event_metadata>   
                <conference_name>conference title</conference_name> 
            </event_metadata> 
            <proceedings_metadata>   
                <proceedings_title>proceedings title</proceedings_title>   
                <publisher>     
                    <publisher_name>publisher</publisher_name>   
                </publisher>  
                <publication_date media_type='print'>     
                    <year>2020</year>   
                </publication_date>   
                <noisbn reason='archive_volume' />   
                <doi_data>     
                    <doi>10.5555/proceedings</doi>     
                    <resource>https://hub.sfedu.ru/proceedings</resource>   
                </doi_data> 
            </proceedings_metadata>
            """
            #==================================================================================================
            # PAPERS SECTION
            #==================================================================================================

            for article in articles:
                article_xml = "<!--  ==============  -->"
                article_xml +='<conference_paper>'


                article_xml += '<contributors>'
                is_first_person = True
                is_first_organization = True
                for author in article.payload['doi_creator']:
                    if author.get('family_name',"").strip()=="" and author.get('given_name',"").strip()=="" and 'affiliation' in author:
                        #if 'family_name' not in author and 'given_name' not in author and 'affiliation' in author:
                        #ORGANIZATION ENTRY
                        article_xml += '<organization sequence="'
                        if is_first_organization:
                            article_xml += 'first'
                        else:
                            article_xml += 'additional'
                        article_xml += '" contributor_role="author">'
                        article_xml += author['affiliation']
                        article_xml += '</organization>'
                        
                        is_first_organization = False
                    else:
                        #PERSON ENTRY

                        article_xml += '<person_name sequence="'
                        if is_first_person:
                            article_xml += 'first'
                        else:
                            article_xml += 'additional'
                        article_xml += '" contributor_role="author">'

                        article_xml += '<given_name>'+author['given_name']+'</given_name>'
                        article_xml += '<surname>'+author['family_name']+'</surname>'
                        if 'affiliation' in author and author['affiliation'].strip() != '':
                            article_xml += '<affiliation>'+author['affiliation']+'</affiliation>'
                        if 'orcid' in author and author['orcid'].strip() != '':
                            article_xml += '<ORCID>https://orcid.org/'+author['orcid'].replace('https://orcid.org/','').replace('/','').replace('–','-')+'</ORCID>'
                        article_xml += '</person_name>'
                        
                        is_first_person = False

                        
                article_xml += '</contributors>'
                article_xml +='<titles>'
                article_xml +='<title>'+article.payload['translated_title'][0]+'</title>'
                article_xml +='<original_language_title>'+article.payload['title'][0]+'</original_language_title>'
                article_xml +='</titles>'


                issue_date=asset.payload['issue_date'][0].strip().replace('.','-')
                issue_date_online=asset.payload['issue_date_online'][0].strip().replace('.','-')
                if(int(issue_date.split('-')[0])>1000):
                    #YYYY-MM-DD
                    print_year = issue_date.split('-')[0]
                    print_month = issue_date.split('-')[1]
                    print_day = issue_date.split('-')[2]
                else:
                    #DD-MM-YYYY
                    print_year = issue_date.split('-')[2]
                    print_month = issue_date.split('-')[1]
                    print_day = issue_date.split('-')[0]
                if(int(issue_date_online.split('-')[0])>1000):
                    #YYYY-MM-DD
                    online_year = issue_date_online.split('-')[0]
                    online_month = issue_date_online.split('-')[1]
                    online_day = issue_date_online.split('-')[2]
                else:
                    #DD-MM-YYYY
                    online_year = issue_date_online.split('-')[2]
                    online_month = issue_date_online.split('-')[1]
                    online_day = issue_date_online.split('-')[0]



                #duplicates issue's publication dates
                article_xml += '<publication_date media_type="print">'
                article_xml += '<month>'+print_month+'</month>'
                article_xml += '<day>'+print_day+'</day>'
                article_xml += '<year>'+print_year+'</year>'
                article_xml += '</publication_date>'
                article_xml += '<publication_date media_type="online">'
                article_xml += '<month>'+online_month+'</month>'
                article_xml += '<day>'+online_day+'</day>'
                article_xml += '<year>'+online_year+'</year>'
                article_xml += '</publication_date>'

                if 'page_range' in article.payload:
                    article_xml +='<pages>'
                    article_xml +='<first_page>'+article.payload['page_range'][0].strip().split('-')[0]+'</first_page>'
                    article_xml +='<last_page>'+article.payload['page_range'][0].strip().split('-')[1]+'</last_page>'
                    article_xml +='</pages>'

                article_xml +='<doi_data>'
                article_xml +='<doi>'+article.payload['doi_request'][0].strip()+'</doi>'
                article_xml +='<resource>'
                article_xml +='https://hub.sfedu.ru/repository/material/'+article.meta.get('uuid',str(article.pk+800000000))+'/?direct_link=true'
                #article_xml +='https://hub.sfedu.ru/repository/material/'+article.meta['uuid']+'/?direct_link=true'
                if 'hyperlink' in article.payload:
                    article_xml += '?direct_link=true'
                article_xml +='</resource>'
                article_xml +='</doi_data>'

                article_xml +='</conference_paper>'
                xml_data += article_xml

            xml_data+='</conference></body>'
            #==================================================================================================
            # FINAL XML ASSEMBLY
            #==================================================================================================

            xml_complete ='<doi_batch xmlns="http://www.crossref.org/schema/4.4.2" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:jats="http://www.ncbi.nlm.nih.gov/JATS1" version="4.4.2" xsi:schemaLocation="http://www.crossref.org/schema/4.4.2 http://www.crossref.org/schema/deposit/crossref4.4.2.xsd">'
            #xml_complete = '<doi_batch xmlns="http://www.crossref.org/schema/4.3.7" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:jats="http://www.ncbi.nlm.nih.gov/JATS1" version="4.3.7" xsi:schemaLocation="http://www.crossref.org/schema/4.3.7 http://www.crossref.org/schema/deposit/crossref4.3.7.xsd">'
            xml_complete += xml_head
            xml_complete += xml_data
            xml_complete += '</doi_batch>'
            xml_complete = xml_complete.replace('&','&amp;')
            #==================================================================================================
            # POST TO CROSSREF
            #==================================================================================================

            login = 'sfedu'
            password = '<crossref password>'
            url = 'https://doi.crossref.org/servlet/deposit?operation=doMDUpload&login_id='+login+'&login_passwd='+password
            doi = asset.payload['doi_request'][0].strip().replace(' ','')
            filename = doi.split('/')[1]+'.xml'
            path = '/var/www/localhost/django/hub/media/doi_metadata/'
            with open(path+filename, 'w') as static_file:
                static_file.write(xml_complete)

            if debug:
                print(doi)
                print(path+filename)
                print(url)

            files = {'file': open(path+filename, 'rb')}

            r = requests.post(url, files=files)
            if debug:
                print(r.status_code)
                print(r.text)
            if r.status_code>299 or r.status_code < 200:
                if 'history' not in asset.meta:
                    asset.meta['history'] = list()
                asset_history_record = dict()
                asset_history_record['datetime'] = str(datetime.datetime.now())
                asset_history_record['action'] = 'MINT DOI FAIL '+asset.payload['doi_request'][0]
                if asset.operator:
                    asset_history_record['operator'] = asset.operator.pk
                else:
                    asset_history_record['operator'] = 0

                asset_history_record['stationinroute'] = asset.stationinroute.pk
                asset.meta['history'].append(asset_history_record)
                asset.payload['doi_minting_fail_description'] = ['CROSSREF RESPONSE: '+r.text]
                asset.save()
            else:
                if 'doi_minting_fail_description' in asset.payload:
                    dump = asset.payload.pop('doi_minting_fail_description',None)
                    asset.save()
                if 'history' not in asset.meta:
                    asset.meta['history'] = list()
                asset_history_record = dict()
                asset_history_record['datetime'] = str(datetime.datetime.now())
                asset_history_record['action'] = 'MINT DOI SUCCESS '+doi
                if asset.operator:
                    asset_history_record['operator'] = asset.operator.pk
                else:
                    asset_history_record['operator'] = 0

                asset_history_record['stationinroute'] = asset.stationinroute.pk
                asset.meta['history'].append(asset_history_record)
                asset.save()
                asset.payload['doi'] = [doi]
                dump = asset.payload.pop('doi_request',None)
                asset.save()

                for article in articles:
                    article.payload['periodical_title'] = [journal.payload['title'][0]]
                    article.save()
                    article.stationinroute.route.route_asset(article,destination_id=98)

        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            #print(exc_type, fname, exc_tb.tb_lineno)
            if 'history' not in asset.meta:
                asset.meta['history'] = list()
            asset_history_record = dict()
            asset_history_record['datetime'] = str(datetime.datetime.now())
            asset_history_record['action'] = 'MINT DOI FAIL '+asset.payload['doi_request'][0]
            if asset.operator:
                asset_history_record['operator'] = asset.operator.pk
            else:
                asset_history_record['operator'] = 0

            asset_history_record['stationinroute'] = asset.stationinroute.pk
            asset.meta['history'].append(asset_history_record)
            asset.payload['doi_minting_fail_description'] = [str(e)+'; '+str(exc_type)+'; '+str(fname)+'; '+str(exc_tb.tb_lineno)]
            asset.save()
        
        return

class DOIPacketMintingStation:
    def perform(self, asset):
        debug = True
        import uuid
        import datetime
        from nexus.models import Asset
        import requests
        #make xml, post to crossref, send to problem station if needed, send articles to doi minting,
        #'doi_minting_fail_description'
        if 'doi' in asset.payload: #don't assign doi more than once ever
            asset_history_record = dict()
            asset_history_record['datetime'] = str(datetime.datetime.now())
            asset_history_record['action'] = 'MINT DOI ABORT - ASSET ALREADY HAS IT'
            if asset.operator:
                asset_history_record['operator'] = asset.operator.pk
            else:
                asset_history_record['operator'] = 0

            asset_history_record['stationinroute'] = asset.stationinroute.pk
            asset.meta['history'].append(asset_history_record)
            asset.save()
            return

        try:
            journal = Asset.objects.filter(type__id=41).filter(payload__title__0=asset.payload['doi_allocation_quota_source'][0])[0]
        except:
            journal = None
        #articles = Asset.objects.filter(type__id=60).filter(payload__journal_issue_for_doi__0 = str(asset.pk))
        articles = Asset.objects.filter(type__id__in=[39,60]).filter(payload__journal_issue_for_doi__0 = str(asset.pk))

        dt=datetime.datetime.now()
        try:
            username = User.objects.get(pk=asset.meta['creator']).username
        except:
            username = "abogomolov"
        try:
            #==================================================================================================
            # MANDATORY HEAD SECTION
            #==================================================================================================
            xml_head = "<head>"
            xml_head += "<doi_batch_id>"+str(asset.pk)+"</doi_batch_id>"
            xml_head += "<timestamp>"+str(dt.year)+str(dt.month).rjust(2,'0')+str(dt.day).rjust(2,'0')+str(dt.hour).rjust(2,'0')+str(dt.minute).rjust(2,'0')+"</timestamp>"
            xml_head += "<depositor>"
            xml_head += "<depositor_name>sfedu</depositor_name>"

            #xml_head += "<email_address>"+username+"@sfedu.ru</email_address>"

            xml_head += "<email_address>abogomolov@sfedu.ru</email_address>"
                
            xml_head += "</depositor>"
            xml_head += "<registrant>Southern Federal University</registrant>"
            xml_head += "</head>"

            #==================================================================================================
            # JOURNAL METADATA SECTION
            #==================================================================================================
            xml_journal_metadata = "<journal_metadata>"
            if 'title_en' in journal.payload:
                xml_journal_metadata += "<full_title>"+journal.payload['title_en'][0]+"</full_title>"
            else:
                xml_journal_metadata += "<full_title>"+journal.payload['title'][0]+"</full_title>"
            
            if 'short_title' in journal.payload:
                xml_journal_metadata += "<abbrev_title>"+journal.payload['short_title'][0]+"</abbrev_title>"
            if 'issn' in journal.payload:
                xml_journal_metadata += '<issn media_type="print">'+journal.payload['issn'][0].replace(' ','').replace('-','')+'</issn>'
            if 'eissn' in journal.payload:
                xml_journal_metadata += '<issn media_type="electronic">'+journal.payload['eissn'][0].replace(' ','').replace('-','')+'</issn>'
            if 'doi' in journal.payload and 'hyperlink' in journal.payload:
                xml_journal_metadata += '<doi_data>'
                xml_journal_metadata += '<doi>'+journal.payload['doi'][0].strip()+'</doi>'
                xml_journal_metadata += '<resource>'+journal.payload['hyperlink'][0].strip()+'</resource>'
                xml_journal_metadata += '</doi_data>'

            xml_journal_metadata += "</journal_metadata>"


            #==================================================================================================
            # CURRENT ISSUE SECTION
            #==================================================================================================
            issue_date=asset.payload['issue_date'][0].strip().replace('.','-')
            issue_date_online=asset.payload['issue_date_online'][0].strip().replace('.','-')
            if(int(issue_date.split('-')[0])>1000):
                #YYYY-MM-DD
                print_year = issue_date.split('-')[0]
                print_month = issue_date.split('-')[1]
                print_day = issue_date.split('-')[2]
            else:
                #DD-MM-YYYY
                print_year = issue_date.split('-')[2]
                print_month = issue_date.split('-')[1]
                print_day = issue_date.split('-')[0]
            if(int(issue_date_online.split('-')[0])>1000):
                #YYYY-MM-DD
                online_year = issue_date_online.split('-')[0]
                online_month = issue_date_online.split('-')[1]
                online_day = issue_date_online.split('-')[2]
            else:
                #DD-MM-YYYY
                online_year = issue_date_online.split('-')[2]
                online_month = issue_date_online.split('-')[1]
                online_day = issue_date_online.split('-')[0]

            xml_journal_issue = "<journal_issue>"

            xml_journal_issue += '<publication_date media_type="print">'
            xml_journal_issue += '<month>'+print_month+'</month>'
            xml_journal_issue += '<day>'+print_day+'</day>'
            xml_journal_issue += '<year>'+print_year+'</year>'
            xml_journal_issue += '</publication_date>'

            xml_journal_issue += '<publication_date media_type="online">'
            xml_journal_issue += '<month>'+online_month+'</month>'
            xml_journal_issue += '<day>'+online_day+'</day>'
            xml_journal_issue += '<year>'+online_year+'</year>'
            xml_journal_issue += '</publication_date>'

            if 'tome' in asset.payload:
                xml_journal_issue += '<journal_volume><volume>'+str(asset.payload['tome'][0])+'</volume></journal_volume>'
            xml_journal_issue += '<issue>'+str(asset.payload['issue_number'][0])+'</issue>'

            if 'hyperlink' in asset.payload:
                xml_journal_issue += '<doi_data>';
                xml_journal_issue += '<doi>'+str(asset.payload['doi_request'][0].strip().replace(' ',''))+'</doi>'
                xml_journal_issue += '<resource>'+str(asset.payload['hyperlink'][0].strip())+'</resource>'
                xml_journal_issue += '</doi_data>'

            xml_journal_issue += "</journal_issue>"
            #==================================================================================================
            # ARTICLES SECTION
            #==================================================================================================
            xml_journal_articles = ""
            for article in articles:
                article_xml = "<!--  ==============  -->"
                article_xml +='<journal_article publication_type="full_text">'

                article_xml +='<titles>'
                article_xml +='<title>'+article.payload['translated_title'][0]+'</title>'
                article_xml +='<original_language_title>'+article.payload['title'][0]+'</original_language_title>'
                article_xml +='</titles>'

                article_xml += '<contributors>'
                is_first_person = True
                is_first_organization = True
                for author in article.payload['doi_creator']:
                    if author.get('family_name',"").strip()=="" and author.get('given_name',"").strip()=="" and 'affiliation' in author:
                        #ORGANIZATION ENTRY
                        article_xml += '<organization sequence="'
                        if is_first_organization:
                            article_xml += 'first'
                        else:
                            article_xml += 'additional'
                        article_xml += '" contributor_role="author">'
                        article_xml += author['affiliation']
                        article_xml += '</organization>'
                        
                        is_first_organization = False
                    else:
                        #PERSON ENTRY

                        article_xml += '<person_name sequence="'
                        if is_first_person:
                            article_xml += 'first'
                        else:
                            article_xml += 'additional'
                        article_xml += '" contributor_role="author">'

                        article_xml += '<given_name>'+author['given_name']+'</given_name>'
                        article_xml += '<surname>'+author['family_name']+'</surname>'
                        if 'affiliation' in author and author['affiliation'].strip() != '':
                            article_xml += '<affiliation>'+author['affiliation']+'</affiliation>'
                        if 'orcid' in author and author['orcid'].strip() != '':
                            article_xml += '<ORCID>https://orcid.org/'+author['orcid'].replace('https://orcid.org/','').replace('/','').replace('–','-')+'</ORCID>'
                        article_xml += '</person_name>'
                        
                        is_first_person = False

                        
                article_xml += '</contributors>'
                if 'abstract_en' in article.payload and len(article.payload['abstract_en'])>0:
                    article_xml+='<jats:abstract><jats:p>'+article.payload['abstract_en'][0].replace('\r\n',' ').replace('  ',' ')+'</jats:p></jats:abstract>'

                #duplicates issue's publication dates
                article_xml += '<publication_date media_type="print">'
                article_xml += '<month>'+print_month+'</month>'
                article_xml += '<day>'+print_day+'</day>'
                article_xml += '<year>'+print_year+'</year>'
                article_xml += '</publication_date>'
                article_xml += '<publication_date media_type="online">'
                article_xml += '<month>'+online_month+'</month>'
                article_xml += '<day>'+online_day+'</day>'
                article_xml += '<year>'+online_year+'</year>'
                article_xml += '</publication_date>'

                if 'page_range' in article.payload:
                    article_xml +='<pages>'
                    article_xml +='<first_page>'+article.payload['page_range'][0].strip().split('-')[0]+'</first_page>'
                    article_xml +='<last_page>'+article.payload['page_range'][0].strip().split('-')[1]+'</last_page>'
                    article_xml +='</pages>'

                article_xml +='<doi_data>'
                article_xml +='<doi>'+article.payload['doi_request'][0].strip()+'</doi>'
                article_xml +='<resource>'
                article_xml +='https://hub.sfedu.ru/repository/material/'+article.meta.get('uuid',str(article.pk+800000000))+'/?direct_link=true'
                if 'hyperlink' in article.payload:
                    article_xml += '?direct_link=true'
                article_xml +='</resource>'
                article_xml +='</doi_data>'

                article_xml +='</journal_article>'
                xml_journal_articles += article_xml

            #==================================================================================================
            # FINAL XML ASSEMBLY
            #==================================================================================================
            xml_body = "<body><journal>"
            xml_body += xml_journal_metadata
            xml_body += xml_journal_issue
            xml_body += xml_journal_articles
            xml_body += "</journal></body>"
            xml_complete = '<doi_batch xmlns="http://www.crossref.org/schema/4.3.7" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:jats="http://www.ncbi.nlm.nih.gov/JATS1" version="4.3.7" xsi:schemaLocation="http://www.crossref.org/schema/4.3.7 http://www.crossref.org/schema/deposit/crossref4.3.7.xsd">'
            xml_complete += xml_head
            xml_complete += xml_body
            xml_complete += '</doi_batch>'
            
            #print(xml_complete)
            xml_complete = xml_complete.replace('&','&amp;')
            #==================================================================================================
            # POST TO CROSSREF
            #==================================================================================================

            login = 'sfedu'
            password = '<crossref password>'
            url = 'https://doi.crossref.org/servlet/deposit?operation=doMDUpload&login_id='+login+'&login_passwd='+password
            doi = asset.payload['doi_request'][0].strip().replace(' ','')
            filename = doi.split('/')[1]+'.xml'
            path = '/var/www/localhost/django/hub/media/doi_metadata/'
            with open(path+filename, 'w') as static_file:
                static_file.write(xml_complete)

            if debug:
                print(doi)
                print(path+filename)
                print(url)

            files = {'file': open(path+filename, 'rb')}

            r = requests.post(url, files=files)
            if debug:
                print(r.status_code)
                print(r.text)
            if r.status_code>299 or r.status_code < 200:
                if 'history' not in asset.meta:
                    asset.meta['history'] = list()
                asset_history_record = dict()
                asset_history_record['datetime'] = str(datetime.datetime.now())
                asset_history_record['action'] = 'MINT DOI FAIL '+asset.payload['doi_request'][0]
                if asset.operator:
                    asset_history_record['operator'] = asset.operator.pk
                else:
                    asset_history_record['operator'] = 0

                asset_history_record['stationinroute'] = asset.stationinroute.pk
                asset.meta['history'].append(asset_history_record)
                asset.payload['doi_minting_fail_description'] = ['CROSSREF RESPONSE: '+r.text]
                asset.save()
            else:
                if 'doi_minting_fail_description' in asset.payload:
                    dump = asset.payload.pop('doi_minting_fail_description',None)
                    asset.save()
                if 'history' not in asset.meta:
                    asset.meta['history'] = list()
                asset_history_record = dict()
                asset_history_record['datetime'] = str(datetime.datetime.now())
                asset_history_record['action'] = 'MINT DOI SUCCESS '+doi
                if asset.operator:
                    asset_history_record['operator'] = asset.operator.pk
                else:
                    asset_history_record['operator'] = 0

                asset_history_record['stationinroute'] = asset.stationinroute.pk
                asset.meta['history'].append(asset_history_record)
                asset.save()
                asset.payload['doi'] = [doi]
                dump = asset.payload.pop('doi_request',None)
                asset.save()

                for article in articles:
                    article.payload['periodical_title'] = [journal.payload['title'][0]]
                    article.save()
                    article.stationinroute.route.route_asset(article,destination_id=98)

        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            #print(exc_type, fname, exc_tb.tb_lineno)
            if 'history' not in asset.meta:
                asset.meta['history'] = list()
            asset_history_record = dict()
            asset_history_record['datetime'] = str(datetime.datetime.now())
            asset_history_record['action'] = 'MINT DOI FAIL '+asset.payload['doi_request'][0]
            if asset.operator:
                asset_history_record['operator'] = asset.operator.pk
            else:
                asset_history_record['operator'] = 0

            asset_history_record['stationinroute'] = asset.stationinroute.pk
            asset.meta['history'].append(asset_history_record)
            asset.payload['doi_minting_fail_description'] = [str(e)+'; '+str(exc_type)+'; '+str(fname)+'; '+str(exc_tb.tb_lineno)]
            asset.save()
        
        return




class UuidStation:

    def perform(self,asset):
        logger = logging.getLogger("hub_logger")
        logger.debug('UuidStation invoked')
        if 'uuid' not in asset.payload:
            logger.debug('Assigning uuid to asset ...')
            #asset_uuid = str(uuid.uuid4())
            asset_uuid = str(uuid.uuid4())
            asset.payload['uuid'] = list()
            asset.payload['uuid'].append(asset_uuid)

            asset_history_record = dict()
            asset_history_record['datetime'] = str(datetime.datetime.now())
            asset_history_record['action'] = u'ASSIGN UUID '+ asset_uuid
            asset_history_record['operator'] = None
            asset_history_record['stationinroute'] = asset.stationinroute.pk

            #from nexus.models import Asset
            #asset = Asset.objects.get(pk=asset.pk)

            if 'history' not in asset.meta:
                asset.meta['history'] = list()
            asset.meta['history'].append(asset_history_record)
            asset.save()

            logger.debug('...done: %s'%asset_uuid)
        else:
            logger.debug('Asset already has uuid=%s, doing nothing' % asset.payload['uuid'][0])


def ConvertToPdfStation_threaded_proc(asset_id):
    from nexus.models import Asset
    asset = Asset.objects.get(pk=asset_id)
    #asset_id=asset.pk

    debug = True#False
    if debug:
        print('ConvertToPdsStation_threaded_proc('+str(asset_id)+')')

    #os.environ['HOME'] = '/tmp'
    
    #print '['+os.environ['HOME']+']'
    #dump = asset.payload.pop('ConvertToPdfStation_error',None)
    if debug:
        print('asset obtained')
    #print "ConvertToPdfStation perform fired"
    #The station presumes that it must convert the files that are represented in routing requirements, i.e. asset.stationinroute.properties.routing[0]
    #It takes default (1st) route in routing and takes the names of filefields to convert; it presumes that filefield name is <masterfield>.content_type and trims .content_type

    routing_requirements = None
    try:
        routing_requirements = asset.stationinroute.properties['routing'][0]['requirements']
    except:
        return
    if debug:
        print('routing requirements obtained')

    for routing_requirement in routing_requirements:
        sysname = routing_requirement['sysname'].replace('.content_type','')
        if debug:
            print("checking requirement for "+sysname)
        if '.' in sysname: #nested field
            payload_key = sysname.split('.')[0]
            nested_key = sysname.split('.')[1]

            if debug:
                print("nested field:["+payload_key+"]["+nested_key+"]")
            if payload_key not in asset.payload:
                if debug:
                    print( payload_key+" is not present in payload, skip")
                continue

            masterfield = None

            try:
                from nexus.models import MasterField
                masterfield = MasterField.objects.get(sysname=sysname)
                if debug:
                    print("masterfield "+sysname+" obtained")
                if not masterfield.properties.get('is_filefield',False):
                    if debug:
                        print( "masterfield is not filefield, skipping")
                    continue

                for compound_field in asset.payload[payload_key]:
                    file_dict = compound_field[nested_key]
                    try:
                        file_type = file_dict['content_type']
                        if file_type == 'application/pdf':
                            continue
                        from nexus.models import FileStorage
                        storage_path = FileStorage.objects.get(pk=file_dict['storage']).path
                        asset_path = str(asset.pk)+'/'+file_dict['uuid']

                        original_file_name = file_dict['filename']
                        original_file_title,original_file_ext = os.path.splitext(original_file_name)

                        original_file_path = storage_path+asset_path+'/'

                        modified_file_name = original_file_title+'.pdf'
                        try:
                            shell_cmd = u'libreoffice --headless --invisible --convert-to pdf --outdir "'+original_file_path+'" "'+original_file_path+original_file_name+'"'
                            if debug:
                                print(shell_cmd)
                            subprocess.call(shell_cmd,shell=True)
                        except Exception as e:
                            if debug:
                                print(e)
                            continue
                        #print "libreoffice done"
                        if os.path.exists(original_file_path+modified_file_name):
                            if debug:
                                print("modified file found")
                            file_dict['content_type'] = 'application/pdf'
                            file_dict['filename'] = modified_file_name
                            file_dict['size'] = os.path.getsize(original_file_path+modified_file_name)

                            asset_history_record = dict()
                            asset_history_record['datetime'] = str(datetime.datetime.now())
                            asset_history_record['action'] = u'CONVERT TO PDF '+ sysname + u' ('+original_file_name+u' to '+modified_file_name+')'
                            asset_history_record['operator'] = None
                            asset_history_record['stationinroute'] = asset.stationinroute.pk

                            #from nexus.models import Asset
                            #asset = Asset.objects.get(pk=asset_id)

                            if 'history' not in asset.meta:
                                asset.meta['history'] = list()
                            asset.meta['history'].append(asset_history_record)
                            asset.save()

                            asset.stationinroute.station.perform(asset)
                            asset.route.asset_action(asset)
                        else:
                            asset_history_record = dict()
                            asset_history_record['datetime'] = str(datetime.datetime.now())
                            asset_history_record['action'] = u'CONVERT TO PDF FAILED '+ sysname + u' ('+original_file_name+u' to '+modified_file_name+')'
                            asset_history_record['operator'] = None
                            asset_history_record['stationinroute'] = asset.stationinroute.pk

                            #from nexus.models import Asset
                            #asset = Asset.objects.get(pk=asset_id)

                            if 'history' not in asset.meta:
                                asset.meta['history'] = list()
                            asset.meta['history'].append(asset_history_record)

                            if 'ConvertToPdfStation_error' not in asset.payload:
                                asset.payload['ConvertToPdfStation_error'] = list()
                            asset.payload['ConvertToPdfStation_error'].append(u"Попытка конвертирования файла в формат pdf не удалась")

                            asset.save()


                    except Exception as e:
                        if debug:
                            print(e)
                        continue
            except Exception as e:
                if debug:
                    print(e)
                    exc_type, exc_obj, exc_tb = sys.exc_info()
                    print(exc_tb.tb_lineno)

                continue
        else: #non-nested field
            if debug:
                print("non-nested field "+sysname)
            if sysname not in asset.payload:
                if debug:
                    print("field is absent in payload, skipping")
                continue
            #print "field is in payload"
            masterfield = None

            try:
                if debug:
                    print("trying to get masterfield ["+sysname+"]")
                from nexus.models import MasterField
                masterfield = MasterField.objects.get(sysname=sysname)
                if debug:
                    print("masterfield "+sysname+" obtained")
                if not masterfield.properties.get('is_filefield',False):
                    if debug:
                        print("masterfield is not filefield, skipping")
                    continue

                #print "masterfield is filefield"
                #try to convert
                for file_dict in asset.payload[sysname]:
                    try:
                        file_type = file_dict['content_type']
                        if file_type == 'application/pdf':
                            if debug:
                                print("file_type is application/pdf, skipping"+str(file_dict))
                            continue
                        from nexus.models import FileStorage

                        storage_path = FileStorage.objects.get(pk=file_dict['storage']).path
                        asset_path = str(asset.pk)+'/'+file_dict['uuid']

                        original_file_name = file_dict['filename']
                        original_file_title,original_file_ext = os.path.splitext(original_file_name)
                        original_file_path = storage_path+asset_path+'/'

                        modified_file_name = original_file_title+'.pdf'
                        try:
                            shell_cmd = 'libreoffice --headless --invisible --convert-to pdf --outdir "'+original_file_path+'" "'+original_file_path+original_file_name+'"'
                            if debug:
                                print(shell_cmd)
                            
                            process = subprocess.Popen(shell_cmd, stdout=subprocess.PIPE, stdin=subprocess.PIPE, shell=True)
                            stdout, stderr = process.communicate()
                            if debug:
                                print('CONVERSION ENDED. STDOUT:',str(stdout),",STDERR:",str(stderr))
                        except Exception as e:
                            if debug:
                                print("ERROR CONVERTING:",e)
                            asset_history_record = dict()
                            asset_history_record['datetime'] = str(datetime.datetime.now())
                            asset_history_record['action'] = u'CONVERT TO PDF FAILED '+ sysname + u' ('+original_file_name+u' to '+modified_file_name+')'
                            asset_history_record['operator'] = None
                            asset_history_record['stationinroute'] = asset.stationinroute.pk

                            #from nexus.models import Asset
                            #asset = Asset.objects.get(pk=asset_id)

                            if 'history' not in asset.meta:
                                asset.meta['history'] = list()
                            asset.meta['history'].append(asset_history_record)
                            asset.save()
                            if debug:
                                print('error converting')
                                print(e)
                                exc_type, exc_obj, exc_tb = sys.exc_info()
                                print(exc_tb.tb_lineno)

                            continue

                        if os.path.exists(original_file_path+modified_file_name):
                            if debug:
                                print("modified file found")
                            file_dict['content_type'] = 'application/pdf'
                            file_dict['filename'] = modified_file_name
                            file_dict['size'] = os.path.getsize(original_file_path+modified_file_name)

                            asset_history_record = dict()
                            asset_history_record['datetime'] = str(datetime.datetime.now())
                            asset_history_record['action'] = u'CONVERT TO PDF '+ sysname + u' ('+original_file_name+u' to '+modified_file_name+')'
                            asset_history_record['operator'] = None
                            asset_history_record['stationinroute'] = asset.stationinroute.pk

                            #from nexus.models import Asset
                            #asset = Asset.objects.get(pk=asset_id)

                            if 'history' not in asset.meta:
                                asset.meta['history'] = list()
                            asset.meta['history'].append(asset_history_record)
                            asset.save()
                            if debug:
                                print("file dict modifications: "+str(file_dict)+" merged into "+str(asset.payload[sysname]))

                        else:
                            asset_history_record = dict()
                            asset_history_record['datetime'] = str(datetime.datetime.now())
                            asset_history_record['action'] = u'CONVERT TO PDF FAILED '+ sysname + u' ('+original_file_name+u' to '+modified_file_name+')'
                            asset_history_record['operator'] = None
                            asset_history_record['stationinroute'] = asset.stationinroute.pk

                            #from nexus.models import Asset
                            #asset = Asset.objects.get(pk=asset_id)
                            if debug:
                                print("modified file NOT found at "+original_file_path+modified_file_name)

                            if 'history' not in asset.meta:
                                asset.meta['history'] = list()
                            asset.meta['history'].append(asset_history_record)

                            if 'ConvertToPdfStation_error' not in asset.payload:
                                asset.payload['ConvertToPdfStation_error'] = list()
                            asset.payload['ConvertToPdfStation_error'].append(u"Попытка конвертирования файла в формат pdf не удалась")

                            asset.save()

                    except Exception as e:
                        if debug:
                            print(e)
                            exc_type, exc_obj, exc_tb = sys.exc_info()
                            print(exc_tb.tb_lineno)
                        continue

            except Exception as e:
                if debug:
                    print(e)
                    exc_type, exc_obj, exc_tb = sys.exc_info()
                    print(exc_tb.tb_lineno)

                continue
        if debug:
            print('ConvertToPdsStation_threaded_proc('+str(asset_id)+') ended')
        asset.save()
        asset.refresh_from_db()
        asset.route.asset_action(asset)
        return

class ConvertToPdfStation:
    def perform(self,asset):
        debug = True
        if debug:
            print('ConvertToPdfStation def perform(self,asset) start')
        convert_thread = threading.Thread(target=ConvertToPdfStation_threaded_proc,args=(asset.pk,))
        convert_thread.daemon = False
        convert_thread.start()
        if debug:
            print('ConvertToPdfStation def perform(self,asset) end')
        return

class SimpleIdentificationStation:
    #one creator, one publication_creator
    def perform(self,asset):
        debug = False#True
        if debug:
            print('SimpleIdentificationStation def perform(self,asset) start')
        
        try:
            u=User.objects.get(pk=asset.meta['creator'])
        except:
            return
            
        if 'publication_creators' not in asset.meta:
            asset.meta['publication_creators'] = []
        if 'publication_creators_complete' not in asset.meta:
            asset.meta['publication_creators_complete'] = False
        
        found_creator = False
        for creator in asset.meta['publication_creators']:
            if creator['username'] == u.username:
                found_creator = True
                break
        if found_creator:
            return
        
        asset.meta['publication_creators'].append({'last_name':u.last_name,'first_name':u.first_name,'username':u.username,'pk':u.pk})
        asset.meta['publication_creators_complete'] = True
        asset.save()
        
        return

def MaterialAllocateEmailNotification_threaded_proc(asset_id):
    from django.contrib.auth.models import User
    from nexus.utils.email.smtp import nexus_sendmail
    from nexus.models import Asset
    asset = Asset.objects.get(pk=asset_id)

    try:
        if 'creator' in asset.meta:
            creator = User.objects.get(pk=asset.meta['creator']).username
            to = creator+'@sfedu.ru'
        else:
            to = 'abogomolov@sfedu.ru'

        dictionary = {
            'to': to,
            'subject': u'hub.sfedu.ru - размещение материала #'+asset.payload['uuid'][0]+u' ('+asset.type.type_name+u')',
            'body': '<p>Здравствуйте!<p>Ваш материал был успешно добавлен в хранилище Портала электронных ресурсов Южного федерального университета.<p>Материалу присвоен следующий трек-номер:<div style="display:inline-block;font-size:300%;padding:5px;border:2px solid black;">'+asset.payload['uuid'][0]+'</div>.<p>Перейти к размещенному материалу можно по следующей ссылке: <a href="http://hub.sfedu.ru/material/'+asset.payload['uuid'][0]+'/">http://hub.sfedu.ru/material/'+asset.payload['uuid'][0]+'/</a>.',
            'DSN':True
        }
        nexus_sendmail(dictionary)
        asset_history_record = dict()
        asset_history_record['datetime'] = str(datetime.datetime.now())
        asset_history_record['action'] = u'EMAIL NOTIFICATION SENT '
        asset_history_record['operator'] = None
        asset_history_record['stationinroute'] = asset.stationinroute.pk
        #print "...done"

        #from nexus.models import Asset
        #asset = Asset.objects.get(pk=asset_id)

        if 'history' not in asset.meta:
            asset.meta['history'] = list()
        asset.meta['history'].append(asset_history_record)
        asset.save()
    except:
        pass

    return



class MicrosoftOfficePdfStation:
    def send_file_to_conversion(self,asset_id,filefield_,fieldname,debug):
        if debug:
            print('MicrosoftOfficePdfStation.send_file_to_conversion(...)')
            print('asset_id=',asset_id)
            print('fielfield=',filefield)
            print('fieldname=',fieldname)
        try:
            filefield=filefield_.copy()
            filefield['filepath'] = '/var/www/localhost/django/hub/media/nexus_assets/'+str(asset_id)+'/'+filefield['uuid']+'/'+filefield['filename']
            dictionary = {
                'to': 'hub@sfedu.ru',
                'subject': '[CONVERT TO PDF] asset_id='+str(asset_id)+"|fieldname="+fieldname+"|uuid="+filefield['uuid'],
                'body': '',
                'attachment':[filefield],
                'DSN':True
            }
            if debug:
                print('about to nexus_sendmail the following dict:',dictionary)
            nexus_sendmail(dictionary)
            if debug:
                print('...done')
            return True
        except Exception as e:
            if debug:
                print('Error sending mail:',e)
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                print(exc_type, fname, exc_tb.tb_lineno)
 
            return False
        
        

    @classmethod
    def webhook(cls,request):
        debug = True
        if debug:
            print('MicrosoftOfficePdfStation.webhook')
        if request.META.get('HTTP_APIKEY','') != '<api key>':
            response = HttpResponse(content="WRONG API KEY",status=403)
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response["Access-Control-Max-Age"] = "1000"
            response["Access-Control-Allow-Headers"] = "*"
            if debug:
                print('WRONG API KEY:',request.META.get('HTTP_APIKEY',''),', exiting')
            return response

        topic = request.META.get('HTTP_MSGTOPIC',None)
        if not topic:
            response = HttpResponse(content="MISSING MSGTOPIC",status=400)
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response["Access-Control-Max-Age"] = "1000"
            response["Access-Control-Allow-Headers"] = "*"
            if debug:
                print('MISSING MSGTOPIC, exiting')
            return response
        if request.META.get('HTTP_RESULT','failure') != 'success':
            response = HttpResponse("OK")
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response["Access-Control-Max-Age"] = "1000"
            response["Access-Control-Allow-Headers"] = "*"
            dictionary = {
                'to': 'abogomolov@sfedu.ru',
                'subject': 'Конвертирование файла завершилось неудачей',
                'body': topic,
                'DSN':True
            }
            nexus_sendmail(dictionary)
            return response
            
        asset_id = ''
        fieldname = ''
        uuid = ''
        try:
            asset_id = topic.replace('[CONVERT TO PDF] ','').split('|')[0].replace('asset_id=','')
            fieldname = topic.replace('[CONVERT TO PDF] ','').split('|')[1].replace('fieldname=','')
            uuid = topic.replace('[CONVERT TO PDF] ','').split('|')[2].replace('uuid=','')
        except:
            response = HttpResponse(content="WRONG MSGTOPIC FORMAT",status=400)
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response["Access-Control-Max-Age"] = "1000"
            response["Access-Control-Allow-Headers"] = "*"
            if debug:
                print('WRONG MSGTOPIC FORMAT:',topic,', exiting')

            return response
        
        a=None
        try:
            a=Asset.objects.get(pk=asset_id)
        except:
            response = HttpResponse(content="WRONG MSGTOPIC.ASSET_ID",status=400)
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response["Access-Control-Max-Age"] = "1000"
            response["Access-Control-Allow-Headers"] = "*"
            if debug:
                print('WRONG MSGTOPIC.ASSET_ID:',asset_id,', exiting')
            return response
        
        if fieldname not in a.payload:
            response = HttpResponse(content="WRONG MSGTOPIC.FIELDNAME",status=400)
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response["Access-Control-Max-Age"] = "1000"
            response["Access-Control-Allow-Headers"] = "*"
            if debug:
                print('WRONG MSGTOPIC.FIELDNAME:',fieldname,', exiting')
            return response
        
        found = False
        for item in a.payload[fieldname]:
            if isinstance(item,dict) and 'uuid' in item:
                if item['uuid'] == uuid:
                    found = True
                    if not item.get('in_conversion',False):
                        response = HttpResponse(content="WRONG MSGTOPIC.UUID - NOT IN CONVERSION",status=400)
                        response["Access-Control-Allow-Origin"] = "*"
                        response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
                        response["Access-Control-Max-Age"] = "1000"
                        response["Access-Control-Allow-Headers"] = "*"
                        if debug:
                            print('WRONG MSGTOPIC.UUID - NOT IN CONVERSION, exiting')
                        return response
                    
                    old_filename = item['filename']
                    pre, ext = os.path.splitext(old_filename)
                    filepath = '/var/www/localhost/django/media/nexus_assets/'+asset_id+'/'+uuid+'/'+pre+'.pdf'
                    f=open(filepath,'wb')
                    cnt=f.write(request.body)
                    f.close()
                    item['content_type'] = 'application/pdf'
                    item['size'] = os.path.getsize(filepath)
                    item['filename'] = pre+'.pdf'
                    dump = item.pop('in_conversion',None)
                    
                    a.save()
                    break

        if not found:
            response = HttpResponse(content="WRONG MSGTOPIC.UUID",status=400)
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response["Access-Control-Max-Age"] = "1000"
            response["Access-Control-Allow-Headers"] = "*"
            if debug:
                print('WRONG MSGTOPIC.UUID:',uuid,', exiting')
            return response

                    
        response = HttpResponse("OK")
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        response["Access-Control-Max-Age"] = "1000"
        response["Access-Control-Allow-Headers"] = "*"
        if debug:
            print("Everything is ok, finishing")
        return response

    def perform(self,asset):
        debug = False
        if debug:
            print('MicrosoftOfficePdfStation.perform(',asset,')')
        if 'convert_to_pdf_fieldname' not in asset.payload:
            if debug:
                print('convert_to_pdf_fieldname not in asset.payload, returning')
            return
            
        for fieldname in asset.payload['convert_to_pdf_fieldname']:
            if debug:
                print('converting payload.',fieldname)
            for item in asset.payload.get(fieldname,[]):
                if item['content_type'] != 'application/pdf':
                    self.send_file_to_conversion(asset.pk,item,fieldname,debug)
                    item['in_conversion'] = True
        asset.save()
        return