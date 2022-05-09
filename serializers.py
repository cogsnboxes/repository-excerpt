import requests
import json
from django.db.models import Q
from django.db.models.expressions import RawSQL
from rest_framework import serializers, status
from django.contrib.auth.models import User,Group
from nexus.models import *
from hub_messages.models import Hub_message_template
class AssetTypeFilterSerializer(serializers.HyperlinkedModelSerializer):
    descriptive_fieldset = serializers.SerializerMethodField()
    def get_descriptive_fieldset(self,obj):
        fields_list = list()
        fieldnames_list = obj.properties.get('descriptive_fieldset',list())
        for asset_type in fieldnames_list:
            try:
                mf = MasterField.objects.get(sysname=asset_type)
            except:
                fields_list.append({'field_sysname':asset_type, 'field_title':'неизвестно'})
                continue

            field = dict()
            field['field_sysname'] = asset_type
            field['field_title'] = mf.properties.get('title',asset_type)
            field['field_type'] = mf.properties.get('type','неизвестно')
            fields_list.append(field)
        return fields_list


    def to_representation(self, obj):
        representation = super(AssetTypeFilterSerializer,self).to_representation(obj)
        try:
            if self.context['request'].query_params.get('show_assets_count',False):
                #pk = self.context['request'].user.pk
                if 'assets' in self.context:
                    assets = self.context['assets']
                else:
                    assets = Asset.objects.all()

                representation['assets_count']=assets.filter(type=obj).count()
            if self.context['request'].query_params.get('show_pending_assets_count',False):
                if 'pending_assets' in self.context:
                    pending_assets = self.context['pending_assets']
                else:
                    pending_assets = Asset.objects.all()
                #representation['assets_count']=Asset.objects.filter(type=obj).filter(Q(meta__creator=pk)|Q(meta__creator_str=str(pk))).count()

                representation['pending_assets_count']=pending_assets.filter(type=obj).count()
        except Exception as e:
            print(e)
            pass
        return representation

    class Meta:
        model = AssetType
        fields = ('url', 'id','type_name','sysname','descriptive_fieldset')
class AssetTypeSerializer(serializers.HyperlinkedModelSerializer):

    def to_representation(self, obj):
        representation = super(AssetTypeSerializer,self).to_representation(obj)
        try:
            if self.context['request'].query_params.get('show_assets_count',False):
                representation['assets_count']=Asset.objects.filter(type=obj).count()
        except:
            pass
        return representation


    class Meta:
        model = AssetType
        fields = ('url', 'id','type_name','sysname')
        
class AssetTypeStatisticsSerializer(serializers.HyperlinkedModelSerializer):
    publications_count = serializers.SerializerMethodField()
    def get_publications_count(self,obj):
        from nexus.views import PUBLICATIONS_STATION
        return Asset.objects.filter(stationinroute__station__pk=PUBLICATIONS_STATION).filter(type=obj).count()

    ui = serializers.SerializerMethodField()
    def get_ui(self,obj):
        rslt = obj.properties.get('ui',{'color':'#ccc','icon_class':'fa fa-file'})
        return rslt

    class Meta:
        model = AssetType
        fields = ('url', 'id','type_name','sysname','publications_count','ui')

class AssetAssetTypeSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = AssetType
        fields = ('id','type_name')

class AssetSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='asset-detail')
    operator = serializers.HyperlinkedRelatedField(lookup_field='username',view_name='user-detail',read_only=True)
    type = AssetAssetTypeSerializer()

 
    can_delete = serializers.SerializerMethodField()
    def get_can_delete(self, obj):
        instance = obj
        try:
            creator_id = -1
            if 'creator' in instance.meta:
                creator_id=int(instance.meta['creator'])
            elif 'creator_str' in instance.meta:
                creator_id=int(instance.meta['creator_str'])
            else:
                creator_id = int(instance.meta.get('history',[{'operator':None}])[0]['operator'])
            user_id = self.context['request'].user.pk
        except:
            user_id = 0
        if user_id != creator_id:
            return False
        return obj.type.properties.get('allow_creator_delete',False)

    creator_operator = serializers.SerializerMethodField()
    def get_creator_operator(self, obj):
        instance = obj
        try:
            creator_operator = instance.stationinroute.station.properties.get('creator_operator',False)
        except:
            creator_operator = False

        return creator_operator

    def to_representation(self, obj):
        representation = super(AssetSerializer,self).to_representation(obj)

        #asset method that gives only suitable set of payload fields based on request user role
        representation['payload'],fields_order = obj.get_fields(self.context['request'].user)
        if self.context['request'].query_params.get('annotate_values',False):
            representation['type']=AssetTypeSerializer(obj.type,many=False, context={'request': self.context['request']}).data
            representation['operator'] = UserSerializer(obj.operator,many=False, context={'request': self.context['request']}).data
            
            try:
                creator = User.objects.get(pk=obj.meta['creator'])
            except:
                try:
                    creator = User.objects.get(pk=int(obj.meta['creator_str']))
                except:
                    creator = None

            if creator:
                representation['creator'] = UserSerializer(creator,many=False, context={'request': self.context['request']}).data

            else:
                representation['creator'] = None

            representation['track_id'] = obj.meta.get('uuid',None)


            representation['stationinroute']=StationInRouteSerializer(obj.stationinroute,many=False, context={'request': self.context['request']}).data
            representation['route']=RouteSerializer(obj.route,many=False, context={'request': self.context['request']}).data
            representation['field_titles'] = list()
            for key in fields_order:
                if key not in representation['payload']: continue

                try:
                    mf = MasterField.objects.get(sysname=key)
                    title = mf.properties['title']
                    asset_type = mf.properties.get('type','')
                    is_filefield = mf.properties.get('is_filefield',False)
                    is_compound = mf.properties.get('field_compound',False)
                    if is_compound:
                        title_subfield = mf.properties.get('title_subfield',None)
                        if title_subfield is not None:
                            title_subfield = title_subfield.replace(mf.sysname+'.','')
                    else:
                        title_subfield = None
                    result = {"title":title,"name":key,"type":asset_type,"is_filefield":is_filefield,"is_compound":is_compound}

                    if title_subfield is not None:
                        result['title_subfield'] = title_subfield
                        try:
                            result["title_subfield_type"] = MasterField.objects.get(sysname=key+'.'+title_subfield).properties.get('type','input')

                        except:
                            pass

                    representation['field_titles'].append(result)
                except:
                    pass
        has_right = False
        if (self.context['request'].user.pk == obj.meta.get('creator',0) and obj.stationinroute.station.properties.get('creator_operator',False)) or (self.context['request'].user == obj.operator) or (self.context['request'].user in obj.stationinroute.station.operators.all()) or (self.context['request'].user in obj.stationinroute.station.supervisors.all()):
            has_right = True

        if self.context['request'].query_params.get('routing_info',False) and has_right:
            #representation['routing_info'] = obj.stationinroute.route.check_routing_requirements(obj)
            routing_list = obj.stationinroute.route.check_routing_requirements(obj)
            representation['routing_info'] = []
            for item in routing_list:#representation['routing_info']:
                if item['destination_id'] == obj.stationinroute.pk:
                    continue
                    
                destination_id = item['destination_id']
                if item['destination_id'] == '#RETURN#':
                    try:
                        destination_id = RouteRecord.objects.filter(asset=obj)[0].stationinroute.pk
                    except Exception as e:
                        print('Error during getting destination_id from #RETURN#')
                        print(e)
                        return
                else:
                    destination_id = item['destination_id']

                destination=StationInRoute.objects.get(pk=destination_id)
                you_operate_it=self.context['request'].user in destination.station.operators.all()
                you_supervise_it=self.context['request'].user in destination.station.supervisors.all()
                dump = item.pop('destination_id',None)
                if destination.route == obj.stationinroute.route:
                    destination_name = destination.station.station_name
                else:
                    destination_name = destination.station.station_name+' ('+destination.route.route_name+')'
                
                item['destination'] = {
                    "destination_id":destination.pk,
                    "destination_name":destination_name,
                    "you_operate_it":you_operate_it,
                    "you_supervise_it":you_supervise_it
                }
                representation['routing_info'].append(item)
        
        from nexus.views import publications_assettypes,REPOSITORY_ENTRANCE_STATION
        if obj.type.pk in publications_assettypes and RouteRecord.objects.filter(asset=obj).filter(stationinroute__station__id=REPOSITORY_ENTRANCE_STATION).count() > 0 and 'uuid' in obj.meta:
            representation['repository_link'] = '/repository/material/'+obj.meta['uuid']+'/'
        

        return representation

    title = serializers.SerializerMethodField()
    def get_title(self, obj):
        return obj.get_signature_string()
        
    station_name = serializers.SerializerMethodField()
    def get_station_name(self, obj):
        return obj.stationinroute.station.station_name

    creation_datetime = serializers.SerializerMethodField()
    def get_creation_datetime(self, obj):
        if 'creation_datetime' in obj.meta:
            return obj.meta['creation_datetime'].split(".")[0]
        else:
            return obj.meta.get('history',[{"datetime":''}])[0]['datetime'].split(".")[0]


    class Meta:
        model = Asset
        fields = ('url','id','title','type','creation_datetime','payload','stationinroute','route','station_name','operator','creator_operator','can_delete')
        read_only_fields = ('url','id','title','type','creation_datetime','stationinroute','route','operator','creator_operator','can_delete')

        #fields = ('url', 'payload','type')


class ReviewSerializer(serializers.HyperlinkedModelSerializer):

    review_text = serializers.SerializerMethodField()
    def get_review_text(self, obj):
        return obj.payload.get('review_text',[''])[0].replace('\n','<br>')

    review_mark = serializers.SerializerMethodField()
    def get_review_mark(self, obj):
        return obj.payload.get('review_mark',[''])[0]

    review_author = serializers.SerializerMethodField()
    def get_review_author(self, obj):
        return obj.payload.get('review_author',[''])[0]

    review_datetime = serializers.SerializerMethodField()
    def get_review_datetime(self, obj):
        if 'review_datetime' in obj.payload:
            return obj.payload['review_datetime'][0].split(".")[0]
        else:
            return ''

    review_target = serializers.SerializerMethodField()
    def get_review_target(self, obj):
        try:
            uuid = obj.payload['material_id'][0]
            trgt = Asset.objects.get(meta__uuid=uuid)
            title = trgt.get_signature_string()
            asset_type = trgt.type.type_name
            return {'link':'https://hub.sfedu.ru/repository/material/'+uuid+'/','title':title,'type':asset_type}
        except:
            return None

    class Meta:
        model = Asset
        fields = ('review_target','review_text','review_mark','review_author','review_datetime')
        read_only_fields = ('review_target','review_text','review_mark','review_author','review_datetime')

        #fields = ('url', 'payload','type')

class EducationalMarkSerializer(serializers.HyperlinkedModelSerializer):

    creation_datetime = serializers.SerializerMethodField()
    def get_creation_datetime(self, obj):
        if 'creation_datetime' in obj.payload:
            return obj.payload['creation_datetime'][0].split(".")[0]
        else:
            return ''

    mark = serializers.SerializerMethodField()
    def get_mark(self, obj):
        if 'educational_mark' in obj.payload:
            return obj.payload['educational_mark'][0]
        else:
            return ''
    mark_type = serializers.SerializerMethodField()
    def get_mark_type(self, obj):
        if 'educational_mark_type' in obj.payload:
            return obj.payload['educational_mark_type'][0]
        else:
            return ''

    material = serializers.SerializerMethodField()
    def get_material(self, obj):
        try:
            uuid = obj.payload['material_id'][0]
            trgt = Asset.objects.get(meta__uuid=uuid)
            title = trgt.get_signature_string()
            asset_type = trgt.type.type_name
            return {'link':'https://hub.sfedu.ru/repository/material/'+uuid+'/','title':title,'type':asset_type,'track_id':uuid}
        except:
            return None

    class Meta:
        model = Asset
        fields = ('creation_datetime','mark','mark_type','material')
        read_only_fields = ('creation_datetime','mark','mark_type','material')

class AssetRouteRecordSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='asset-detail')
    type = AssetAssetTypeSerializer()

    title = serializers.SerializerMethodField()
    def get_title(self, obj):
        return obj.get_signature_string()

    creation_datetime = serializers.SerializerMethodField()
    def get_creation_datetime(self, obj):
        if 'creation_datetime' in obj.meta:
            return obj.meta['creation_datetime'].split(".")[0]
        else:
            return obj.meta.get('history',[{"datetime":''}])[0]['datetime'].split(".")[0]

    def to_representation(self,obj):
        representation = super(AssetRouteRecordSerializer,self).to_representation(obj)

        from nexus.views import publications_assettypes,REPOSITORY_ENTRANCE_STATION
        if obj.type.pk in publications_assettypes and RouteRecord.objects.filter(asset=obj).filter(stationinroute__station__id=REPOSITORY_ENTRANCE_STATION).count() > 0 and 'uuid' in obj.meta:
            representation['repository_link'] = '/repository/material/'+obj.meta['uuid']+'/'
        

        return representation

    class Meta:
        model = Asset
        fields = ('url','id','title','type','creation_datetime')
        read_only_fields = ('url','id','title','type','creation_datetime')

        #fields = ('url', 'payload','type')


class AssetDeferredSerializer(serializers.HyperlinkedModelSerializer):

    def to_representation(self, obj):
        representation = super(AssetDeferredSerializer,self).to_representation(obj)

        #asset method that gives only suitable set of payload fields based on request user role
        representation['payload'] = obj.get_fields(self.context['request'].user)

        return representation

    class Meta:
        model = Asset
        fields = ('url',)
        read_only_fields = ('url',)
        #fields = ('url', 'payload','type')

class AssetPublicationSerializer(serializers.HyperlinkedModelSerializer):
    signature_string = serializers.SerializerMethodField()
    def get_signature_string(self,obj):
        return obj.get_signature_string()

    publication_creators = serializers.SerializerMethodField()
    def get_publication_creators(self,obj):
        creators = obj.meta.get('publication_creators',list())
        #do not assume that creator is author - explicitly put him in publication_creators during allocation

        return creators

    publication_creators_complete = serializers.SerializerMethodField()
    def get_publication_creators_complete(self,obj):
        creators_complete = obj.meta.get('publication_creators_complete',False)
        return creators_complete

    fulltext_check_complete = serializers.SerializerMethodField()
    def get_fulltext_check_complete(self,obj):
        check_complete = obj.payload.get('fulltext_check_complete',[False])[0]
        return check_complete

    publication_reviews_summary = serializers.SerializerMethodField()
    def get_publication_reviews_summary(self,obj):

        reviews=Asset.objects.filter(type__id=52).filter(payload__material_id=[obj.meta.get('uuid','ERROR')]).order_by('pk')
        result = []
        for review in reviews:
            item = {}

            item['review_mark']=review.payload.get('review_mark',[None])[0]
            result.append(item)
        return result


    track_id = serializers.SerializerMethodField()
    def get_track_id(self,obj):
        if 'uuid' in obj.meta:
            return obj.meta['uuid']
        else:
            return ''
    
    creator = serializers.SerializerMethodField()
    def get_creator(self,obj):
        instance = obj
        try:
            creator_id = -1
            if 'creator' in instance.meta:
                creator_id=int(instance.meta['creator'])
            elif 'creator_str' in instance.meta:
                creator_id=int(instance.meta['creator_str'])
            else:
                creator_id = int(instance.meta.get('history',[{'operator':None}])[0]['operator'])
            user = User.objects.get(pk=creator_id)
            return {'creator_id':creator_id,'username':user.username}
        except:
            return {'creator_id':0,'username':'unknown username'}

    can_edit = serializers.SerializerMethodField()
    def get_can_edit(self,obj):
        instance = obj
        user = self.context['request'].user
        try:
            creator_id = -1
            if 'creator' in instance.meta:
                creator_id=int(instance.meta['creator'])
            elif 'creator_str' in instance.meta:
                creator_id=int(instance.meta['creator_str'])
            else:
                creator_id = int(instance.meta.get('history',[{'operator':None}])[0]['operator'])
            creator = User.objects.get(pk=creator_id)
        except:
            creator = None

        if creator == user:
            return True
        if user == instance.operator:
            return True
        if user in instance.stationinroute.station.operators.all():
            return True
        if user in instance.stationinroute.station.supervisors.all():
            return True
        if user in instance.stationinroute.route.supervisors.all():
            return True

        return False

    doi = serializers.SerializerMethodField()
    def get_doi(self,obj):
        if 'doi' in obj.payload:
            return obj.payload['doi'][0]
        else:
            return None

    isbn = serializers.SerializerMethodField()
    def get_isbn(self,obj):
        if 'isbn' in obj.payload:
            return obj.payload['isbn'][0]
        else:
            return None

    def to_representation(self, obj):
        representation = super(AssetPublicationSerializer,self).to_representation(obj)

        representation['payload'],fields_order = obj.get_fields(None)
        representation['field_titles'] = list()
        for key in fields_order:
            if key not in representation['payload']: continue
            """
            for i,item in enumerate(representation['payload'][key]):
                if isinstance(representation['payload'][key][i],int):
                    representation['payload'][key][i] = str(representation['payload'][key][i])
            """
            try:
                mf = MasterField.objects.get(sysname=key)
                title = mf.properties['title']
                asset_type = mf.properties.get('type','')
                is_filefield = mf.properties.get('is_filefield',False)
                is_compound = mf.properties.get('field_compound',False)
                if is_compound:
                    title_subfield = mf.properties.get('title_subfield',None)
                    if title_subfield is not None:
                        title_subfield = title_subfield.replace(mf.sysname+'.','')
                else:
                    title_subfield = None

                result = {"title":title,"name":key,"type":asset_type,"is_filefield":is_filefield,"is_compound":is_compound}

                if title_subfield is not None:
                    result['title_subfield'] = title_subfield
                    try:
                        result["title_subfield_type"] = MasterField.objects.get(sysname=key+'.'+title_subfield).properties.get('type','input')

                    except:
                        pass
                #representation['field_titles'].append({'title':title,"name":key,"type":asset_type,"is_filefield":is_filefield})
                representation['field_titles'].append(result)
            except:
                #no masterfield, treat as input with title=sysname
                representation['field_titles'].append({'title':key,"name":key,"type":"input","is_filefield":False})
        if 'journal_issue_for_doi' in obj.payload:
            try:
                mf = MasterField.objects.get(sysname='journal_issue_for_doi')
                title = mf.properties['title']
                asset_type = mf.properties.get('type','')
                is_filefield = mf.properties.get('is_filefield',False)
                is_compound = True
                result = {"title":title,"name":'journal_issue_for_doi',"type":asset_type,"is_filefield":is_filefield,"is_compound":is_compound}
                representation['field_titles'].append(result)
                a=Asset.objects.get(pk=obj.payload['journal_issue_for_doi'][0])
                issn = None
                eissn = None
                journal_hyperlink = None
                issns = Asset.objects.filter(type__id=41).filter(payload__title__0=a.payload.get('doi_allocation_quota_source',[''])[0])
                if issns.count()>0:
                    issn = issns.first().payload.get('issn',[None])[0]
                    eissn = issns.first().payload.get('eissn',[None])[0]
                    journal_hyperlink = issns.first().payload.get('hyperlink',[None])[0]
                representation['payload']['journal_issue_for_doi']=[{
                    'doi':a.payload.get('doi',[''])[0],
                    'hyperlink':a.payload.get('hyperlink',[''])[0],
                    'journal_hyperlink':journal_hyperlink,
                    'issue_date':a.payload.get('issue_date',[''])[0],
                    'title_type':a.payload.get('title_type',[''])[0],
                    'issue_number':a.payload.get('issue_number',[''])[0],
                    'issn':issn,
                    'eissn':eissn,
                    'issue_date_online':a.payload.get('issue_date_online',[''])[0],
                    'doi_allocation_quota_source':a.payload.get('doi_allocation_quota_source',[''])[0],
                    
                }]
            except:
                pass
        if hasattr(self.context['request'],'query_params') and  self.context['request'].query_params.get('show_marks',False):
            if obj.type.id == 51:
                marks = Asset.objects.filter(type__id=53).filter(payload__material_id__0=obj.meta.get('uuid',None))
                if marks.count() == 0:
                    representation['marks'] = []
                else:
                    representation['marks'] = []
                    for item in marks:
                        representation['marks'].append({
                            'educational_mark':item.payload.get('educational_mark',[None])[0],
                            'educational_mark_type':item.payload.get('educational_mark_type',[None])[0],
                            'username':item.payload.get('username',[None])[0],
                            'creation_datetime':item.payload.get('creation_datetime',[None])[0],
                            
                        })
        return representation
    
    type = AssetAssetTypeSerializer()
    
    creation_datetime = serializers.SerializerMethodField()
    def get_creation_datetime(self, obj):
        if 'creation_datetime' in obj.meta:
            return obj.meta['creation_datetime'].split(".")[0]
        else:
            return obj.meta.get('history',[{"datetime":''}])[0]['datetime'].split(".")[0]

    allocation_datetime = serializers.SerializerMethodField()
    def get_allocation_datetime(self, obj):
        from nexus.views import REPOSITORY_ENTRANCE_STATION
        route_records = RouteRecord.objects.filter(asset=obj).filter(stationinroute__station__id=REPOSITORY_ENTRANCE_STATION).order_by('-pk')
        if route_records.count() > 0:
            return str(route_records.last().record_datetime)
        else:
            return None

        
    class Meta:
        model = Asset
        fields = ('id','allocation_datetime','creation_datetime', 'creator', 'can_edit', 'publication_creators','publication_creators_complete','fulltext_check_complete', 'publication_reviews_summary','type', 'signature_string','track_id','doi','isbn')


class AssetPeriodicalSerializer(serializers.HyperlinkedModelSerializer):
    subscribed_periodical_title = serializers.SerializerMethodField()
    def get_subscribed_periodical_title(self,obj):
        return obj.payload.get('subscribed_periodical_title',['',])[0]

    hyperlink = serializers.SerializerMethodField()
    def get_hyperlink(self,obj):
        return obj.payload.get('hyperlink',['',])[0]


    class Meta:
        model = Asset
        fields = ('id','subscribed_periodical_title','hyperlink')

def sigla_to_address(sigla):
    siglas_dict={
        'ЗНБ-АНЛ':'Факультет экономический (Пушкинская, 148)',
        'ЗНБ-АУЛ':'Факультет экономический (Пушкинская, 148)',
        'ЗНБ-АХЛ':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-БО4':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-ГГ':'Институт наук о Земле (Зорге, 21Ж)',
        'ЗНБ-ДИР':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-ИБО':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-ИППК':'Библиотека на Пушкинской (Пушкинская, 148)',
        'ЗНБ-КНР':'Библиотека на Пушкинской (Пушкинская, 148)',
        'ЗНБ-КОМПЛ':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-КХ':'Библиотека на Пушкинской (Пушкинская, 148)',
        'ЗНБ-ММ':'Институт математики, механики и компьютерных наук им. И. И. Воровича (Мильчакова, 8А)',
        'ЗНБ-НМО':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-ОИЛ':'Библиотека на Пушкинской (Пушкинская, 148)',
        'ЗНБ-ОКТ':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-ОНО':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-ОРИ':'Библиотека на Пушкинской (Пушкинская, 148)',
        'ЗНБ-СРБ':'Библиотека на Пушкинской (Пушкинская, 148)',
        'ЗНБ-ФВТ':'Институт высоких технологий и пьезотехники (Мильчакова, 10)',
        'ЗНБ-ФПС':'Академия психологии и педагогики (Днепровский, 116)',
        'ЗНБ-ФР':'Институт социологии и регионоведения (Пушкинская, 160)',
        'ЗНБ-ФФ':'Факультет физический (Зорге, 5)',
        'ЗНБ-ФФ-ЧЗ':'Факультет физический (Зорге, 5)',
        'ЗНБ-ХФ':'Факультет химический (Зорге, 7)',
        'ЗНБ-ОО':'Библиотека кампуса на западном (Зорге, 21Ж)',
        'ЗНБ-ЭФ':'Факультет экономический (Пушкинская, 148)',
        'ЗНБ-ЭФ-ЧЗ':'Факультет экономический (Пушкинская, 148)',
        'ЗНБ-АРХИ':'Академия архитектуры и искусств (Буденновский, 39)',
        'ЗНБ-ТТИ':'Библиотека г. Таганрог (Таганрог, ул. Чехова, 22, корпус А)',
        'ПИ':'Библиотека на Садовой (Садовая,33)',
        'ЗНБ-БС':'Библиотека на Садовой (Садовая,33)',
        'ЗНБ-ЛЕН':'Академия психологии и педагогики (Днепровский, 116)',
        'ЗНБ-ЧК':'Библиотека на Днепровском (Днепровский, 116)',
        'ЗНБ-ХГ':'Академия архитектуры и искусств (Горького, 75)'
    }
    try:
        if sigla.upper().strip() in siglas_dict.keys():
            return siglas_dict[sigla.upper().strip()]
        elif sigla.upper().strip().replace('-ЧЗ','') in siglas_dict.keys():
            return siglas_dict[sigla.upper().strip().replace('-ЧЗ','')]
        else:
            return sigla
    except:
        return sigla
class AssetPeriodicalDetailedSerializer(serializers.HyperlinkedModelSerializer):
    subscribed_periodical_title = serializers.SerializerMethodField()
    def get_subscribed_periodical_title(self,obj):
        return obj.payload.get('subscribed_periodical_title',['',])[0]

    hyperlink = serializers.SerializerMethodField()
    def get_hyperlink(self,obj):
        return obj.payload.get('hyperlink',['',])[0]


    issues_quantity = serializers.SerializerMethodField()
    def get_issues_quantity(self,obj):
        issues_count = int(obj.payload.get('legacy_qty',[0])[0])
        if obj.meta.get('source',None) == 'eastview':
            return issues_count
        
        issues = Asset.objects.filter(type__id=27).filter(payload__periodical_name__0=obj.payload['subscribed_periodical_title'][0])
        for issue in issues:
            try:
                issues_count+=int(issue.payload.get('issue_quantity',[1])[0])
            except:
                issues_count+=0
        
        return issues_count

    def to_representation(self, obj):
        representation = super(AssetPeriodicalDetailedSerializer,self).to_representation(obj)
        if self.context['request'].query_params.get('show_issues',False) in ['True','true',True]:
            issues = Asset.objects.filter(type__id=27).filter(payload__periodical_name__0=obj.payload['subscribed_periodical_title'][0]).order_by(RawSQL("payload->>%s", ("issue_year",)),RawSQL("payload->>%s", ("issue_number",)))
            journal_numbers = {}
            for issue in issues:
                sigla = issue.payload.get('issue_location',['?'])[0]
                if sigla not in journal_numbers:
                    journal_numbers[sigla] = {}
                year = issue.payload.get('issue_year',['?'])[0]
                if year not in journal_numbers[sigla]:
                    journal_numbers[sigla][year] = {}
                issue_number = issue.payload.get('issue_number',[''])[0]
                if issue_number not in journal_numbers[sigla][year]:
                    try:
                        journal_numbers[sigla][year][issue_number] = int(issue.payload.get('issue_quantity',[1]))[0]
                    except:
                        journal_numbers[sigla][year][issue_number] = 1
                else:
                    try:
                        journal_numbers[sigla][year][issue_number] += int(issue.payload.get('issue_quantity',[1]))[0]
                    except:
                        journal_numbers[sigla][year][issue_number] += 1
            issues = []
            for sigla in journal_numbers:
                sigla_dict = {}
                sigla_dict['title'] = sigla_to_address(sigla)
                sigla_dict['years'] = []
                for year in journal_numbers[sigla]:
                    year_dict = {}
                    year_dict['title']=year
                    year_dict['issues'] = []
                    for issue in journal_numbers[sigla][year]:
                        issue_dict = {}
                        issue_dict['title']=issue
                        issue_dict['quantity']=journal_numbers[sigla][year][issue]
                        year_dict['issues'].append(issue_dict)
                    year_dict['issues'].sort(key=lambda x: x['title'])
                    sigla_dict['years'].append(year_dict)
                sigla_dict['years'].sort(key=lambda x: x['title'])

                issues.append(sigla_dict)

            representation['issues'] = issues

        return representation


    class Meta:
        model = Asset
        fields = ('id','subscribed_periodical_title','hyperlink','issues_quantity')



class AssetBriefSerializer(serializers.HyperlinkedModelSerializer):
    signature_string = serializers.SerializerMethodField()
    def get_signature_string(self,obj):
        return obj.get_signature_string()

    operator = serializers.SerializerMethodField()
    def get_operator(self,obj):
        queryset=obj.operator
        return UserSerializer(queryset,many=False, context={'request': self.context['request']}).data

    station = serializers.SerializerMethodField()
    def get_station(self,obj):
        queryset=obj.stationinroute.station
        return StationBriefSerializer(queryset,many=False, context={'request': self.context['request']}).data

    creation_datetime = serializers.SerializerMethodField()
    def get_creation_datetime(self, obj):
        if 'creation_datetime' in obj.meta:
            return obj.meta['creation_datetime'].split(".")[0]
        else:
            return obj.meta.get('history',[{"datetime":''}])[0]['datetime'].split(".")[0]

        
    class Meta:
        model = AssetType
        fields = ('id','creation_datetime', 'signature_string','station','operator')

class StationAssetBriefSerializer(serializers.HyperlinkedModelSerializer):
    type = AssetAssetTypeSerializer()

    title = serializers.SerializerMethodField()
    def get_title(self,obj):
        return obj.get_signature_string()

    operator = serializers.SerializerMethodField()
    def get_operator(self,obj):
        queryset=obj.operator
        return UserSerializer(queryset,many=False, context={'request': self.context['request']}).data

    station = serializers.SerializerMethodField()
    def get_station(self,obj):
        queryset=obj.stationinroute.station
        return StationBriefSerializer(queryset,many=False, context={'request': self.context['request']}).data

    creation_datetime = serializers.SerializerMethodField()
    def get_creation_datetime(self, obj):
        if 'creation_datetime' in obj.meta:
            return obj.meta['creation_datetime'].split(".")[0]
        else:
            return obj.meta.get('history',[{"datetime":''}])[0]['datetime'].split(".")[0]

        
    class Meta:
        model = AssetType
        fields = ('id','type','creation_datetime', 'title','station','operator')

class RouteSerializer(serializers.HyperlinkedModelSerializer):
    default_asset_type = AssetTypeSerializer()

    you_supervise_it = serializers.SerializerMethodField()
    def get_you_supervise_it(self,obj):
        return self.context['request'].user in obj.supervisors.all()

    ui = serializers.SerializerMethodField()
    def get_ui(self,obj):
        return obj.properties.get('ui',{})

    assets_quantity = serializers.SerializerMethodField()
    def get_assets_quantity(self,obj):
        if not obj.properties.get('ui',{'hide_quantity':False}).get('hide_quantity',False):
            return Asset.objects.filter(stationinroute__route=obj).count()
        else:
            return None

    non_operator_adding_assets = serializers.SerializerMethodField()
    def get_non_operator_adding_assets(self, obj):
        return obj.route_stations.filter(station__properties__non_operator_adding_assets=True).count() > 0


    operators = serializers.SerializerMethodField()
    def get_operators(self,obj):
        queryset=User.objects.filter(nexus_stations__station_routes__route=obj).distinct()
        return UserSerializer(queryset,many=True, context={'request': self.context['request']}).data

    supervisors = serializers.SerializerMethodField()
    def get_supervisors(self,obj):
        queryset=obj.supervisors.all()
        data = UserSerializer(queryset,many=True, context={'request': self.context['request']}).data
        return data

    assets_finished_count = serializers.SerializerMethodField()
    def get_assets_finished_count(self,obj):
        srs = StationInRoute.objects.filter(route=obj).filter(properties__has_key='exit_station').filter(properties__exit_station=True)
        if srs.count() > 0:
            return RouteRecord.objects.filter(stationinroute__in=srs).count()
        return Asset.objects.filter(stationinroute__route=obj).filter(stationinroute__next_station_in_route=None).count()

    assets_in_progress_count = serializers.SerializerMethodField()
    def get_assets_in_progress_count(self,obj):
        return Asset.objects.filter(stationinroute__route=obj).exclude(stationinroute__next_station_in_route=None).exclude(operator=None).count()
        #return Asset.objects.filter(stationinroute__route=obj).exclude(operator=None).count()

    assets_pending_count = serializers.SerializerMethodField()
    def get_assets_pending_count(self,obj):
        return Asset.objects.filter(stationinroute__route=obj).exclude(stationinroute__next_station_in_route=None).filter(operator=None).count()

    class Meta:
        model = Route
        fields = ('url','id','sysname','route_name','default_asset_type','you_supervise_it','ui','assets_quantity','non_operator_adding_assets','operators','supervisors','assets_finished_count','assets_in_progress_count','assets_pending_count')

class RouteBriefSerializer(serializers.HyperlinkedModelSerializer):

    class Meta:
        model = Route
        fields = ('url','sysname','route_name')

class RouteStationInRouteStationSerializer(serializers.HyperlinkedModelSerializer):
    default_asset_type = AssetTypeSerializer()

    you_supervise_it = serializers.SerializerMethodField()
    def get_you_supervise_it(self,obj):
        return self.context['request'].user in obj.supervisors.all()

    assets_quantity = serializers.SerializerMethodField()
    def get_assets_quantity(self,obj):
        if not obj.properties.get('ui',{'hide_quantity':False}).get('hide_quantity',False):
            return Asset.objects.filter(stationinroute__route=obj).count()
        else:
            return None

    non_operator_adding_assets = serializers.SerializerMethodField()
    def get_non_operator_adding_assets(self, obj):
        return obj.route_stations.filter(station__properties__non_operator_adding_assets=True).count() > 0

    class Meta:
        model = Route
        #fields = ('url','sysname','route_name','default_asset_type','you_supervise_it','ui','assets_quantity','non_operator_adding_assets','assets_finished_count','assets_in_progress_count','assets_pending_count')
        fields = ('url','sysname','route_name','default_asset_type','you_supervise_it','assets_quantity','non_operator_adding_assets')


class StationInRouteStationSerializer(serializers.HyperlinkedModelSerializer):
    route = RouteStationInRouteStationSerializer(required=False)

    allow_adding_assets = serializers.SerializerMethodField()
    def get_allow_adding_assets(self, obj):
        return obj.properties.get('allow_adding_assets',False)


    class Meta:
        model = StationInRoute
        depth=1
        fields = ('url','route','allow_adding_assets')


class StationSerializer(serializers.HyperlinkedModelSerializer):
    station_routes = StationInRouteStationSerializer(many=True,required=False)

    unmanned_station = serializers.SerializerMethodField()
    def get_unmanned_station(self, obj):
        return obj.classname != "Station"

    creator_operator = serializers.SerializerMethodField()
    def get_creator_operator(self, obj):
        return obj.properties.get('creator_operator',False)

    you_operate_it = serializers.SerializerMethodField()
    def get_you_operate_it(self,obj):
        return self.context['request'].user in obj.operators.all()

    you_supervise_it = serializers.SerializerMethodField()
    def get_you_supervise_it(self,obj):
        return self.context['request'].user in obj.supervisors.all()

    auto_assign_mode_on = serializers.SerializerMethodField()
    def get_auto_assign_mode_on(self,obj):
        return obj.properties.get('auto_assign_mode_on',False)

    class Meta:
        model = Station
        depth=1
        #fields = ('url','station_name','custom_field')
        fields = ('url','id','station_name','you_operate_it','you_supervise_it','unmanned_station','creator_operator','auto_assign_mode_on','station_routes')

class UserStationSerializer(serializers.HyperlinkedModelSerializer):
    station_routes = StationInRouteStationSerializer(many=True,required=False)

    pending_assets_count = serializers.SerializerMethodField()
    def get_pending_assets_count(self, obj):
        import datetime
        if obj.properties.get('auto_assign_mode_on',False):
            #auto_assign, show 0
            #result = Asset.objects.filter(stationinroute__station=obj).filter(operator=self.context['request'].user).count()
            #print('after',datetime.datetime.now())
            #return result
            return 0
        else:
            #not auto_assign, show operators assets plus free assets
            #print('before',datetime.datetime.now())
            #result = Asset.objects.filter(stationinroute__station=obj).filter(Q(operator=self.context['request'].user)|Q(operator=None)).count()
            result = Asset.objects.filter(stationinroute__station=obj).filter(operator=None).count()
            #print('after',datetime.datetime.now())
            return result

    def to_representation(self, obj):
        representation = super(UserStationSerializer,self).to_representation(obj)
        if self.context['request'].query_params.get('show_stats',None) in [True,'true','True']:
            year = self.context['request'].query_params.get('year',None)
            user = self.context['user']
            rrs = RouteRecord.objects.filter(operator=user).filter(stationinroute__station__pk=obj.pk)
            if year:
                rrs = rrs.filter(record_datetime__year = int(year))
            
            representation['stats'] = {
                'completed':rrs.count()
            }
            if year:
                representation['stats']['year'] = year
        return representation

    class Meta:
        model = Station
        #depth=1
        #fields = ('url','station_name','custom_field')
        fields = ('url','id','station_name','station_routes','pending_assets_count')


class StationBriefSerializer(serializers.HyperlinkedModelSerializer):

    class Meta:
        model = Station
        #fields = ('url','station_name','custom_field')
        fields = ('url','id','station_name')
        
class StationExtendedSerializer(serializers.HyperlinkedModelSerializer):
    
    unmanned_station = serializers.SerializerMethodField()
    def get_unmanned_station(self, obj):
        return obj.classname != "Station"

    creator_operator = serializers.SerializerMethodField()
    def get_creator_operator(self, obj):
        return obj.properties.get('creator_operator',False)

    fields_list = serializers.SerializerMethodField()
    def get_fields_list(self, obj):
        fields_list = dict()
        for asset_type in obj.properties.get('field_templates',dict()):
            fields_list[asset_type] = list()
            field_templates = obj.get_field_templates(asset_type)

            editable_fields = field_templates['editable_fields']#obj.properties['field_templates'][asset_type].get('editable_fields',list())
            appendable_fields = field_templates['appendable_fields']#obj.properties['field_templates'][asset_type].get('appendable_fields',list())
            required_fields = field_templates['required_fields']#obj.properties['field_templates'][asset_type].get('required_fields',list())
            readonly_fields = field_templates['readonly_fields']#obj.properties['field_templates'][asset_type].get('readonly_fields',list())

            #combined editable and appendable, no duplicates
            field_names = editable_fields + list(set(readonly_fields) - set(editable_fields))+ list(set(appendable_fields) - set(editable_fields))
            #field_names = editable_fields + list(set(appendable_fields) - set(editable_fields))

            for field_name in field_names:
                required_field = field_name in required_fields
                readonly_field = field_name in readonly_fields
                appendable_field = field_name in appendable_fields
                try:
                    mf = MasterField.objects.get(sysname=field_name)
                except:
                    fields_list[asset_type].append({'field_name':field_name,'field_title':'неизвестно','required_field':required_field,'readonly_field':readonly_field,'appendable_field':appendable_field})
                    continue
                
                field_title = mf.properties.get('title','неизвестно')
                fields_list[asset_type].append({'field_name':field_name,'field_title':field_title,'required_field':required_field,'readonly_field':readonly_field,'appendable_field':appendable_field})
                               
        return fields_list
    
    class Meta:
        model = Station
        #fields = ('url','station_name','custom_field')
        fields = ('url','id','station_name','unmanned_station','creator_operator','fields_list')

class StationInRouteSerializer(serializers.HyperlinkedModelSerializer):
    station = StationSerializer(required=False)
    route = RouteSerializer(required=False)

    allow_adding_assets = serializers.SerializerMethodField()
    def get_allow_adding_assets(self, obj):
        return obj.properties.get('allow_adding_assets',False)

    non_operator_adding_assets = serializers.SerializerMethodField()
    def get_non_operator_adding_assets(self, obj):
        return obj.station.properties.get('non_operator_adding_assets',False)

    auto_assign_mode_on = serializers.SerializerMethodField()
    def get_auto_assign_mode_on(self, obj):
        return obj.station.properties.get('auto_assign_mode_on',False)
    
    assets_finished_count = serializers.SerializerMethodField()
    def get_assets_finished_count(self,obj):
         return RouteRecord.objects.filter(stationinroute=obj).count() #Asset.objects.filter(stationinroute=obj).filter(stationinroute__next_station_in_route=None).count()
    
    assets_in_progress_count = serializers.SerializerMethodField()
    def get_assets_in_progress_count(self,obj):
        return Asset.objects.filter(stationinroute=obj).exclude(stationinroute__next_station_in_route=None).exclude(operator=None).count()
        #return Asset.objects.filter(stationinroute=obj).exclude(operator=None).count()

    assets_pending_count = serializers.SerializerMethodField()
    def get_assets_pending_count(self,obj):
        return Asset.objects.filter(stationinroute=obj).exclude(stationinroute__next_station_in_route=None).filter(operator=None).count()
        #return Asset.objects.filter(Q(stationinroute=obj) & Q(operator=None)).count()

    operators = serializers.SerializerMethodField()
    def get_operators(self,obj):
        queryset=obj.station.operators.all()
        return UserSerializer(queryset,many=True, context={'request': self.context['request']}).data

    supervisors = serializers.SerializerMethodField()
    def get_supervisors(self,obj):
        queryset=obj.station.supervisors.all()
        return UserSerializer(queryset,many=True, context={'request': self.context['request']}).data


    routing = serializers.SerializerMethodField()
    def get_routing(self,obj):
        #'routing':destination_id,auto_route,requirements title
        routing_list = list()
        for routing_variant in obj.properties.get('routing',list()):
            auto_route = routing_variant['auto_route']
            requirements = list()
            for requirement in routing_variant.get('requirements',list()):
                requirements.append(requirement['title'])

            if routing_variant['destination_id'] == '#RETURN#':
                dest_ = None

            else:
                dest_ = routing_variant['destination_id']


            try:
                destination = StationInRoute.objects.get(pk=dest_)
            except:
                routing_list.append({'auto_route':auto_route,'requirements':requirements,'destination':'неизвестно'})
                continue
            routing_list.append({'auto_route':auto_route,'requirements':requirements,'destination':destination.station.station_name})
        return routing_list


    class Meta:
        model = StationInRoute
        depth=1
        fields = ('url','station','route','allow_adding_assets','non_operator_adding_assets','auto_assign_mode_on','assets_finished_count','assets_in_progress_count','assets_pending_count','operators','supervisors','routing')

class RouteStationInRouteSerializer(serializers.HyperlinkedModelSerializer):
    station = StationExtendedSerializer(required=False)

    is_additional = serializers.SerializerMethodField()
    def get_is_additional(self, obj):
        return hasattr(obj,'is_additional')

    allow_adding_assets = serializers.SerializerMethodField()
    def get_allow_adding_assets(self, obj):
        return obj.properties.get('allow_adding_assets',False)

    non_operator_adding_assets = serializers.SerializerMethodField()
    def get_non_operator_adding_assets(self, obj):
        return obj.station.properties.get('non_operator_adding_assets',False)

    auto_assign_mode_on = serializers.SerializerMethodField()
    def get_auto_assign_mode_on(self, obj):
        return obj.station.properties.get('auto_assign_mode_on',False)

    assets_finished_count = serializers.SerializerMethodField()
    def get_assets_finished_count(self,obj):
         return RouteRecord.objects.filter(stationinroute=obj).count() #Asset.objects.filter(stationinroute=obj).filter(stationinroute__next_station_in_route=None).count()

    assets_in_progress_count = serializers.SerializerMethodField()
    def get_assets_in_progress_count(self,obj):
        return Asset.objects.filter(stationinroute=obj).exclude(stationinroute__next_station_in_route=None).exclude(operator=None).count()

    assets_pending_count = serializers.SerializerMethodField()
    def get_assets_pending_count(self,obj):
        return Asset.objects.filter(stationinroute=obj).exclude(stationinroute__next_station_in_route=None).filter(operator=None).count()
        #return Asset.objects.filter(Q(stationinroute=obj) & Q(operator=None)).count()

    operators = serializers.SerializerMethodField()
    def get_operators(self,obj):
        queryset=obj.station.operators.all()
        return UserSerializer(queryset,many=True, context={'request': self.context['request']}).data

    supervisors = serializers.SerializerMethodField()
    def get_supervisors(self,obj):
        queryset=obj.station.supervisors.all()
        return UserSerializer(queryset,many=True, context={'request': self.context['request']}).data

    notifications = serializers.SerializerMethodField()
    def get_notifications(self,obj):
        rslt = []

        if obj.station.properties.get('notify_operator',False):
            rslt.append({'recipient':'operator','timing':'before','type':'email'})
        for item in obj.station.properties.get('notifications',[]):
            rslt.append({'type':item.get('type',''),'timing':item.get('timing',''),'recipient':item.get('recipient','')})
        return rslt

    routing = serializers.SerializerMethodField()
    def get_routing(self,obj):
        #'routing':destination_id,auto_route,requirements title
        routing_list = list()
        for routing_variant in obj.properties.get('routing',list()):
            if routing_variant['destination_id'] == obj.pk:
                continue
            auto_route = routing_variant['auto_route']
            suspend_further_routing = routing_variant.get('suspend_further_routing',False)
            asset_type_modifications = []
            route_notifications = []
            if 'asset_type_modifications' in routing_variant:
                for atmod in routing_variant['asset_type_modifications']:
                    from_to = atmod.split('->')
                    try:
                        if from_to[0] == '*':
                            at_from = '*'
                        else:
                            at_from = AssetType.objects.get(pk=int(from_to[0])).type_name
                        at_to = AssetType.objects.get(pk=int(from_to[1])).type_name
                        asset_type_modifications.append({'from':at_from,'to':at_to})
                    except:
                        pass
            if 'route_notifications' in routing_variant:
                for rnot in routing_variant['route_notifications']:
                    msg_template = Hub_message_template.objects.get(sysname=rnot['message_template'])
                    notification = {}
                    notification['type'] = msg_template.message_type
                    notification['timing'] = 'after'
                    notification['recipient'] = msg_template.properties.get('recipient','')
                    notification['title'] = msg_template.title
                    route_notifications.append(notification)
                    #address,message_template
                    #type,timing,recipient
                    #pass
            requirements = list()
            for requirement in routing_variant.get('requirements',list()):
                requirements.append(requirement['title'])
            
            try:
                destination = StationInRoute.objects.get(pk=routing_variant['destination_id'])
            except:
                destination = None
                destination_name = 'предыдущая операция'
                #routing_list.append({'auto_route':auto_route,'requirements':requirements,'destination':'неизвестно'})
                #continue
            if destination:
                if destination.route == obj.route:
                    destination_name = destination.station.station_name
                else:
                    destination_name = destination.station.station_name+' ('+destination.route.route_name+')'
                
            routing_list.append({'auto_route':auto_route,'requirements':requirements,'destination':destination_name,'asset_type_modifications':asset_type_modifications,'route_notifications':route_notifications,'suspend_further_routing':suspend_further_routing})
        return routing_list


    class Meta:
        model = StationInRoute
        depth=1
        fields = ('url','is_additional','station','allow_adding_assets','non_operator_adding_assets','auto_assign_mode_on','assets_finished_count','assets_in_progress_count','assets_pending_count','operators','supervisors','routing','notifications')


        
class StationInRouteBriefSerializer(serializers.HyperlinkedModelSerializer):

    class Meta:
        model = StationInRoute
        depth=1
        fields = ('url',)

class StationInRouteRouteRecordSerializer(serializers.HyperlinkedModelSerializer):
    station = StationBriefSerializer(required=True)
    class Meta:
        model = StationInRoute
        depth=1
        fields = ('url','station')

class PermissionSerializer(serializers.HyperlinkedModelSerializer):
    id = serializers.IntegerField()
    user = serializers.HyperlinkedRelatedField(lookup_field='username',view_name='user-detail',read_only=True)
    creator = serializers.HyperlinkedRelatedField(lookup_field='username',view_name='user-detail',read_only=True)
    asset = AssetDeferredSerializer(required=False)
    class Meta:
        model = Nexus_permission
        #fields = ('url','user','is_prohibition',)
        fields='__all__'
        #exclude=('asset',)
        #exclude=('user','group','creator')


class UserSerializer(serializers.HyperlinkedModelSerializer):
    #publications = serializers.HyperlinkedIdentityField(view_name='user-publications')

    class Meta:
        model = User
        #fields = ('url', 'username','publications')
        fields = ('url', 'username','last_name','first_name','email')
        lookup_field='username'


        extra_kwargs = {
            'url': {'lookup_field': 'username'}
        }

class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ('url','name')

class RouteRecordSerializer(serializers.HyperlinkedModelSerializer):
    asset = AssetRouteRecordSerializer(required=False)
    operator = serializers.HyperlinkedRelatedField(lookup_field='username',view_name='user-detail',read_only=True)
    stationinroute = StationInRouteRouteRecordSerializer(required=True)
    next_stationinroute = StationInRouteRouteRecordSerializer(required=True)
    route = RouteBriefSerializer(required=True)
    class Meta:
        model = RouteRecord
        fields = ('url','stationinroute','next_stationinroute','asset','operator','record_datetime','route')

class AssetHistoryRouteRecordSerializer(serializers.HyperlinkedModelSerializer):
    operator = UserSerializer(required=False)
    stationinroute=StationInRouteSerializer(required=False)

    notifications = serializers.SerializerMethodField()
    def get_notifications(self, obj):
        result = list()
        for item in obj.properties.get('notifications',list()):
            user = self.context['request'].user
            #result.append({'type':item.get('type',''),'status':item.get('status',''),"address":item.get("address","")})
            
            if user in obj.stationinroute.station.supervisors.all():
                result.append({'type':item.get('type',''),'status':item.get('status',''),"address":item.get("address","")})
            elif user in obj.stationinroute.station.operators.all() and item.get('recipient','creator') in ['creator','operator']:
                result.append({'type':item.get('type',''),'status':item.get('status',''),"address":item.get("address","")})
            elif user in obj.stationinroute.station.operators.all() and item.get('recipient','creator') == 'creator':
                result.append({'type':item.get('type',''),'status':item.get('status',''),"address":item.get("address","")})
            elif user.pk == obj.asset.meta.get('creator',0) and item.get('recipient','creator') == 'creator':
                result.append({'type':item.get('type',''),'status':item.get('status',''),"address":item.get("address","")})
            elif user in obj.stationinroute.route.supervisors.all():
                result.append({'type':item.get('type',''),'status':item.get('status',''),"address":item.get("address","")})
            
            
        return result

    def to_representation(self, obj):
        representation = super(AssetHistoryRouteRecordSerializer,self).to_representation(obj)
        representation['operator'] = UserSerializer(obj.operator,many=False, context={'request': self.context['request']}).data
        return representation

    class Meta:
        model = RouteRecord
        fields = ('url','stationinroute','operator','record_datetime','notifications')

class PublicationLogEntrySerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = PublicationLogEntry
        fields = ('url','entry_datetime','access_download_file','access_read_file','access_description')

class PublicationLogEntryListSerializer(serializers.HyperlinkedModelSerializer):
    
    track_id = serializers.SerializerMethodField()
    def get_track_id(self, obj):
        try:
            return obj.asset.meta['uuid']
        except:
            return None

    class Meta:
        model = PublicationLogEntry
        fields = ('url', 'track_id', 'entry_datetime','access_download_file','access_read_file','access_description')
