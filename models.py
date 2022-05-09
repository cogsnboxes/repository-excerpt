import uuid
import magic
import shutil
import json
import ast
import os
import sys
import datetime
import codecs
from psycopg2.extras import Json
from django.db import models
from django.http import FileResponse
from django.dispatch import receiver
from django.contrib.postgres.fields import JSONField
from django.contrib.auth.models import User, Group,AnonymousUser
from nexus.custom_stations import *
from django.shortcuts import redirect,render
from nexus.notifications import *
from common.elasticsearch import asset_to_es, asset_delete_es


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[-1].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

class JSONDictField(JSONField):
    pass

class AssetType(models.Model):
    default_settings = dict()
    default_settings['fields'] = dict()
    default_settings['descriptive_fieldset'] = list()
    default_settings['signature_string'] = ""

    properties_help_text = (
        "allowed properties for AssetType:<br>"
        "fields: dict of dicts, each has masterfield sysnames as keys and the followinf subkeys: "
        ">field_multiple(bool),<br>"
        ">default_value(str) - string or constant (DATETIME_NOW,DATE_NOW,TIME_NOW,YEAR_NOW,CURRENT_USER,CURRENT_USER_EMAIL)<br>; compound field has dict instead of string, nested fields' default values are stored there"
        ">use_default_template(bool): use default template or <sysname>.html template<br>"
        ">default_filestorage<br>"
        "help_text(str) - override masterfield's help_text"
        "title(str) - override masterfield's title"
        "<br>"
        "search_index_whitelist - list of fields that must be indexed, discards masterfields' field_indexed(bool) property<br>"
        "search_index_blacklist - list of fields that must not be indexed, applied during indexing after masterfields' field_indexed(bool) property<br>"
        "descriptive_fieldset - list of fields available to anyone, USED FOR FILTERING<br>"
        "search_fieldset - dict of fields available to search over. If absent, descriptive_fields are used as fuzzy. Value is also a dict with the following keys: fuzzy_search(bool) - defaults to True, exclude(bool) - defaults to False.<br>"
        "creator_fieldset - list of fields available to creator<br>"
        "allow_creator_delete - whether creator is allowed to delete assets, default is False<br>"
        "signature_string - string that identifies the asset. can use placeholders of the following format: {#ASSET_ID#}(ASSET_UUID, ASSET_SIGNATURE_STRING, ASSET_TYPE_NAME, ASSET_STATION_NAME, ASSET_OPERATOR_USERNAME, ASSET_CREATOR_USERNAME, ASSET_ROUTE_NAME), {masterfield_name}. Single values get inserted as is, multiple get concatenated with ',' or only first taken if variable name ends with $ sign; nested values are supported. HTML tags are allowed.<br>"
        "ui(dict) - contains color and icon_class keys for visual representation of the asset type"
    )
    type_name = models.CharField(max_length=255,blank=False,null=False)
    sysname = models.CharField(max_length=255,blank=True,null=True)
    properties = JSONDictField(default=default_settings.copy(),blank=True,null=True,help_text=properties_help_text)
    
    def __str__(self):
        return self.type_name

class AssetManager(models.Manager):
    use_for_related_fields = True
    def get_queryset(self):
        return super(AssetManager, self).get_queryset().defer('payload', 'meta')

class Asset(models.Model):
    meta_help_text = (
        "creator(int) - creator user's pk<br>"
        "creator_str(string) - legacy creator user's pk in string format<br>"
        "history([{'datetime','operator','action','stationinroute'},...]) - a log of things happened to asset<br>"
        "original_creator(int) - creator user's pk when asset must not be listed in user's asset<br>"
        "publication_creators_list[{pk,username,last_name,first_name},...] - a list of users that correspond to asset authors<br>"
        "publication_creators_complete(bool) - wether publication creators list is done or not<br>"
        "merged_assets[int,int,...] - a list of assets that have been merged with current<br>"
        "merged_into(int) - pk of asset that have merged current one into itself, used for user redirects and publication stats recount<br>"
    )


    #asset_type
    
    #type = models.ForeignKey(AssetType)
    type = models.ForeignKey(AssetType, on_delete=models.CASCADE)

    #workflow_stage
    route = models.ForeignKey('Route',blank=False,null=False,related_name='assets', on_delete=models.CASCADE )
    stationinroute = models.ForeignKey('StationInRoute',blank=False,null=False,related_name='assets', on_delete=models.CASCADE)
    operator = models.ForeignKey(User, blank=True,null=True,on_delete=models.SET_NULL)
    payload = JSONDictField(default=dict,blank=True,null=True)
    meta = JSONDictField(default=dict,blank=True,null=True,help_text=meta_help_text)

    objects = AssetManager()


    def get_fields(self, user):
        fields_ordered_list = list()
        fieldset = list()

        if ((user==self.operator) and (user is not None)) or (user in self.stationinroute.station.supervisors.all())  or (user in self.stationinroute.station.operators.all()):
            #operator and supervisor get station-defined editable and appendable fields from payload
            field_templates = self.stationinroute.station.get_field_templates(self.type.sysname)
            try:
                editable_fields = field_templates['editable_fields']#self.stationinroute.station.properties['field_templates'][self.type.sysname]['editable_fields']
            except:
                editable_fields = list()
            try:
                appendable_fields = field_templates['appendable_fields']#self.stationinroute.station.properties['field_templates'][self.type.sysname]['appendable_fields']
            except:
                appendable_fields = list()


            fieldset = editable_fields + list(set(appendable_fields) - set(editable_fields))
        elif user and user.pk in [self.meta.get('creator',0), int(self.meta.get('creator_str','0'))]:
            #creator get creator_fieldset if it exists or descriptive_fieldset otherwise
            if (self.type.properties) and ('creator_fieldset' in self.type.properties):
                fieldset = self.type.properties['creator_fieldset']
            elif (self.type.properties) and ('descriptive_fieldset' in self.type.properties):
                fieldset = self.type.properties['descriptive_fieldset']
        elif (self.type.properties) and ('descriptive_fieldset' in self.type.properties):
            #the rest get descriptive_fieldset or nothing if descriptive_fieldset is not defined
            fieldset = self.type.properties['descriptive_fieldset']

        fields_ordered_list = fieldset.copy()

        result_payload = dict()

        #if payload is somehow absent, return empty dict with ordered list of fields
        if not self.payload:
            return result_payload,fields_ordered_list

        #fill result_payload with fields enumerated in fieldset
        for key in self.payload:
            if key in fieldset:
                result_payload[key] = self.payload[key]
                try:
                    mf = MasterField.objects.get(sysname=key)
                    if mf.properties.get('is_filefield',False):
                        for item in result_payload[key]:
                            item['url'] = '/storage/'+str(item['storage'])+'/'+str(self.pk)+'/'+item['uuid']+'/'
                except:
                    pass
                
        return result_payload,fields_ordered_list#self.payload

    def get_signature_string(self):
        try:
            if not self.type.properties.get('signature_string',False):
                return self.type.type_name
            signature_string_template = self.type.properties['signature_string']
            from nexus.input_processing import put_asset_variables_into_string
            return put_asset_variables_into_string(signature_string_template,self)
        except:
            return self.type.type_name
            
    def __str__(self):
        return str(self.id)+" - "+self.type.type_name


 

@receiver(models.signals.pre_delete)
def delete_asset_files(sender, instance, **kwargs):
    if not isinstance(instance, Asset):
        return

    for filestorage in FileStorage.objects.all():
        file_path = filestorage.path+str(instance.pk)+'/'
        try:
            shutil.rmtree(file_path)
        except:
            print("error removing asset files for asset #",str(instance.pk)," ",file_path)
            pass

    
@receiver(models.signals.post_save)
def elasticsearch_update_index(sender, instance, **kwargs):
    if not isinstance(instance, Asset):
        return

    asset_to_es(instance)

@receiver(models.signals.post_delete)
def elasticsearch_delete_from_index(sender, instance, **kwargs):
    if not isinstance(instance, Asset):
        return
    asset_delete_es(instance)

class Nexus_permission_log_entry(models.Model):
    entry_datetime = models.DateTimeField(auto_now=True,)
    permission_action_sysname = models.CharField(max_length=100,blank=True,null=True,)
    entry_result = models.BooleanField(default=False,)
    user = models.ForeignKey(User,blank=True,null=True,related_name='+',on_delete=models.SET_NULL)
    asset_type = models.ForeignKey('AssetType',blank=True,null=True,related_name='+',on_delete=models.SET_NULL,)
    #route = models.ForeignKey('Route',blank=True,null=True,related_name='+',)
    #station = models.ForeignKey('Station',blank=True,null=True,related_name='+',)
    #stationinroute = models.ForeignKey('StationInRoute',blank=True,null=True,related_name='+',)
    asset = models.ForeignKey(Asset,blank=True,null=True,related_name='+',on_delete=models.SET_NULL)
    permission = models.ForeignKey('Nexus_permission',blank=True,null=True,related_name='+',on_delete=models.SET_NULL)

class Nexus_permission(models.Model):
    #permission general properties
    is_prohibition = models.BooleanField(default=False,)
    is_default = models.BooleanField(default=False,)
    creator = models.ForeignKey(User, blank=True, null=True,related_name='+',on_delete=models.SET_NULL)
    reason = models.CharField(max_length=255,blank=True,null=True,)
    description = models.CharField(max_length=255,blank=True,null=True,)
    creation_datetime = models.DateTimeField(auto_now=True,blank=True,null=True,)
    logging = models.IntegerField(default=0,)#0-no logging,1-log failed checks, 2-log success checks, 3-log every check

    
    #permission targets
    asset_type = models.ForeignKey('AssetType',blank=True,null=True,related_name='assigned_permissions',on_delete=models.SET_NULL)
    route = models.ForeignKey('Route',blank=True,null=True,related_name='assigned_permissions',on_delete=models.SET_NULL)
    station = models.ForeignKey('Station',blank=True,null=True,related_name='assigned_permissions',on_delete=models.SET_NULL)
    stationinroute = models.ForeignKey('StationInRoute',blank=True,null=True,related_name='assigned_permissions',on_delete=models.SET_NULL)
    asset = models.ForeignKey('Asset',blank=True,null=True,related_name='+',on_delete=models.SET_NULL)
    #permission_action is also a target
    permission_action_sysname = models.CharField(max_length=100,blank=True,null=True,)
    
    
    #permission_conditions
    datetime_start = models.DateTimeField(blank=True,null=True,)
    datetime_end = models.DateTimeField(blank=True,null=True,)
    user = models.ForeignKey(User,blank=True,null=True,related_name = '+',on_delete=models.SET_NULL)
    group = models.ForeignKey(Group,blank=True,null=True,related_name = 'assigned_permissions',on_delete=models.SET_NULL)
    is_creator = models.BooleanField(default=False,)
    is_operator = models.BooleanField(default=False,)
    is_supervisor = models.BooleanField(default=False,)
    is_authenticated_user = models.BooleanField(default=False,)
    payload_value = JSONDictField(blank=True,null=True,default=dict)#if value is dict: cmp_op is operator ("=",">","<",...),cmp_val is value; otherwise presence is checked (important! key value is not evaluated in this case!)
    ip_range = models.CharField(blank=True,null=True,max_length=255,)
    
    @classmethod
    def check_permissions(cls, user=None, asset_type=None, route=None, station=None, stationinroute=None, asset=None, action=None,debug=False):
        from nexus.permissions import perform_check
        return perform_check(user, asset_type, route, station, stationinroute, asset, action, debug)


    def __str__(self):
            str_repr = "#"+str(self.pk)+" "
            if self.is_prohibition:
                str_repr+="ЗАПРЕТ "
            if self.permission_action_sysname:
                str_repr += self.permission_action_sysname+" "
            if self.user:
                str_repr+= self.user.username+" "
            if self.asset_type:
                str_repr+= "[тип="+self.asset_type.type_name+"]"
            if self.route:
                str_repr+= "[маршрут="+self.route.route_name+"]"
            if self.station:
                str_repr+= "[станция="+self.station.station_name+"]"
            if self.stationinroute:
                str_repr+= "[станция-в-маршруте="+self.stationinroute.station.station_name+"]"
            if self.asset:
                str_repr+= "[ассет="+str(self.asset)+"]"
            if self.ip_range:
                str_repr+= "[IP="+str(self.ip_range)+"]"
            return str_repr

class MasterField(models.Model):
    default_settings = dict()
    default_settings["title"] = "TITLE_NOT_SET"

    sysname = models.CharField(max_length=255,)

    properties_help_text = (
        "allowed properties for compounds (type='compound'):<br>"
        "title_subfield(str) - master field property, contains a name of a subfield that is used as a compound title<br>"
        "field_compound(bool) - master field property, must be True<br>"
        "field_nested(bool) - nested field property, must be True<br>"
        "order(int) - nested field property, contains a numberical order of display<br>"
        "allowed properties for all fields:<br>"
        "htmp_props(str) - they get inserted into corresponding html element, eg 'rows=10' is put into <textares rows=10 [...]>{...] in abstract_en<br>"
        "field_indexed(bool) - whether ElasticSearch should index this field in payload or not<br>"
        "title(str) - title of a field<br>"
        "[checkbox and forcedcheckbox only] caption(str) - caption for a checkbox<br>"
        "is_filefield(bool) - must be True for file fields<br>"
        "allowed_values(list) - list of string values allowed for field<br>"
        "asset_allowed_values_filter(str) - rest filter expression to pick assets as allowed values list, supports only 'type' (asset type id) parameter, e.g ?type=3<br>"
        "asset_allowed_values_descriptive_field(str) - which field to use as title along with asset id as value<br>"
        "asset_allowed_values_use_pk(bool) - if set to true, asset pk will be used as value, otherwise descriptive field. Default is false.<br>"
        "values_strict(bool) - NOT WORKING DUE TO NEW key/value NATURE OF LISTS - whether allowed values are the only options or some other values can exist. If False, shows the current value regardless of its presence in allowed_values,<br>"
        "  otherwise show field as blank. Default is False.<br>"
        "type(str) - type of field, can be absent; if present, determines default control type for field: input, select, textarea, radio, checkbox, forcedcheckbox, compound, datetime, filefield, hyperlink<br>"
        "help_text(str) - help on field, can be overriden by AssetType<br>"
        "default_filestorage(int)<br>"
        "datetime_format(str) - date or time or datetime, for datetime field<br>"
        "allowed properties for inputs<br>"
        "autocomplete_values(list) - list of string values to provide a user with - just like google's<br>"
        "autocomplete_url(str) - url to rest api supplying list of values to provide a user with - just like google's; filter query can be appended to the end of url in the following way: ?query=<br>"
        "asset_autocomplete_values_filter(str) - rest filter expression to pick assets as autocomplete values list, supports 'type' (asset type id) parameter, e.g ?type=3, and creator=SAME_USER, e.g. ?type=3&creator=SAME_USER<br>"
        "asset_autocomplete_values_descriptive_field(str) - which field to use as title along with asset id as value<br>"
    )
    properties = JSONDictField(blank=True,null=True,default=default_settings.copy(),help_text=properties_help_text)

    def __str__(self):
        return self.sysname

from django.http import HttpResponse
import os.path
class FileStorage(models.Model):
    path = models.TextField(blank=True,null=True)
    properties_help_text=(
    "allowed keys: title(str),restrictions(list)?, permissions(list)?<br>"
    )
    properties = JSONDictField(blank=True,null=True,default=dict,help_text=properties_help_text)

    def __str__(self):
        if self.properties.get('title',False):
            return "" + self.properties['title']
        else:
            return "Хранилище без названия " + self.path

    def download_file(self, request, asset, file_,debug=False):
        
        if debug:
            print('FileStorage.download_file called, asset=',asset,', file=',file_)
        detachment_uuid = file_['uuid']
        detachment = None
    
        can_download = False
    

        #must check permissions before downloading
        if request.user.is_authenticated:
            user=request.user
        else:
            user=AnonymousUser()#request.user
        user.ip_address = get_client_ip(request)
        if debug:
            print('User:',user)
        #if request.user == asset.operator:
        if user == asset.operator:
            can_download = True
        if not can_download:
            can_download = Nexus_permission.check_permissions(user=user,asset=asset,action='download')
        if debug:
            print('can_download=',can_download)
        if not can_download:
            if not user or user.is_anonymous == True:
                if debug:
                    print('cant download and no user, redirecting to login, bye!')
                return redirect('/accounts/login/?next=%s' % request.path)
            if debug:
                print('cant download, user is authenticated, refusing to download, bye!')
            return HttpResponse(status=403, content ="Вы не обладаете необходимыми полномочиями для сохранения полного текста файла или автор сделал его недоступным для скачивания")


        file_path_full = FileStorage.objects.get(pk=int(file_['storage'])).path+str(asset.pk)+'/'+file_['uuid']+'/'+file_['filename']
        if debug:
            print('file path:',file_path_full)
        if not os.path.exists(file_path_full):
            if debug:
                print('file path does not exist, bye!')
            return HttpResponse(status=404,content="Файл отсутствует в хранилище.")

        response = HttpResponse(status=200,content_type=file_['content_type'])
        try:
            response['Content-Disposition'] = 'attachment; filename="'+file_['filename']+'";filename*=UTF-8\'\''+file_['filename'].encode('utf-8')
           
        except:
            filename_chunks = file_['filename'].split('.')
            if len(filename_chunks) > 1:
                fileext = filename_chunks[-1:]
            else:
                fileext = ''

            response['Content-Disposition'] = 'attachment; filename='+str(file_['uuid'])+'.'+fileext[0]
        if debug:
            print('about to write file to response object')
        response.content = open(file_path_full,'rb')
        if debug:
            print('...done, returning response, bye!')

        return response


    def display_epub(self, request, asset, epub_uuid,params=[]):
        detachment_uuid = epub_uuid
        detachment = None
    
        can_read = False
    

        #must check permissions before downloading
        if request.user.is_authenticated:
            user=request.user
        else:
            #user = None
            user=request.user

        user.ip_address = get_client_ip(request)

        if request.user == asset.operator:
            can_read = True
        if not can_read:
            can_read = Nexus_permission.check_permissions(user=user,asset=asset,action='read')
        if isinstance(user,AnonymousUser):
            user=None

        if not can_read:
            if not user:
                return redirect('/accounts/login/?next=%s' % request.path)

            return HttpResponse(status=403, content ="Вы не обладаете необходимыми полномочиями для чтения электронной публикации или автор сделал ее недоступной для чтения")

        if len(params)>0:
            params_str='/'.join(params)
            print(params_str)
            file_path_full = self.path+str(asset.pk)+'/'+detachment_uuid+'/'+params_str
            
            #relative path workaround:
            if not os.path.exists(file_path_full):
                for i in range(1,len(params)):
                  test_path = '/'.join(params[i:])
                  
                  if os.path.exists(self.path+str(asset.pk)+'/'+detachment_uuid+'/'+test_path):
                    file_path_full=self.path+str(asset.pk)+'/'+detachment_uuid+'/'+test_path
                    break
        else:
            file_path_full = self.path+str(asset.pk)+'/'+detachment_uuid+'/index.html'
        
        if not os.path.exists(file_path_full):
            print("FILE NOT FOUND:",file_path_full)
            return HttpResponse(status=404,content="Файл отсутствует в хранилище.")

        if file_path_full.lower().endswith('.css'):
            content_type = 'text/css'
        elif file_path_full.lower().endswith('.svg'):
            content_type = 'image/svg+xml'
        elif file_path_full.lower().endswith('.js'):
            content_type = 'application/javascript'
        else:
            mime = magic.Magic(mime=True)
            content_type = mime.from_file(file_path_full)
            
        #print(content_type)

        response = HttpResponse(status=200,content_type=content_type,content=open(file_path_full,'rb'))

        return response




class Route(models.Model):

    sysname = models.CharField(max_length=255,)
    route_name = models.CharField(max_length=255,)
    default_asset_type = models.ForeignKey(AssetType,blank=True,null=True,on_delete=models.SET_NULL)
    properties_help_text = (
        "ui:dict of ui properties for dashboard<br>"
        "    help_url(str): url to help article<br>"
        "    widget_color(str): classname defining the color of a widget, eg bg-aqua<br>"
        "    widget_icon(str): fontawesome icon class for widget, eg fa fa-cogs<br>"
        "    custom_landing_url: if present, widget should make it as default add task button<br>"
        "    hide_widget(bool): if True, does not show a widget for the route in dashboard<br>"
        "    hide_quantity(bool): if True, does not show assets count<br>"
        "    custom_asset_add_string(str): if set, allows for custom asset creation button caption<br>"
        "    custom_add_message(str): if set, allows for custom asset successfully created message<br>"
        "    custom_add_redirect_url(str): if set, redirects after success add to the specified address, otherwise to /dashboard/asset/<id>/ (if reload_on_success_asset_add is not set, of course)<br>"
        "    reload_on_success_asset_add: if true, pressing ok in success message reloads the window for further asset addition<br>"
    )

    properties = JSONDictField(blank=True,null=True,default=dict, help_text=properties_help_text)
    supervisors = models.ManyToManyField(User,blank=True,related_name='nexus_routes_supervised',)

    #performs when some asset is saved
    #invoked by asset post_save signal
    def asset_action(self,asset,suspend_further_routing=False):
        debug=False
        if asset.meta.get('debug',False):
            debug=True
        if debug:
            print(">>> route.asset_action: performing route asset action for asset #",asset.pk,", suspend further_routing=",suspend_further_routing)
        if suspend_further_routing:
            print("further routing suspended")
            return
            print("must have already exited!")
        sr = asset.stationinroute

        if debug:
            print('--- asset_action calls check_routing_requirements')        
        check_result = self.check_routing_requirements(asset)
        #is_validated = False
        for item in check_result:
            is_validated = False
            print("checking route variant: ")
            if item['is_validated']:
                is_validated = True
                #break
            if item['auto_route'] and is_validated:
                print("route.asset_action: asset #",asset.pk," is validated for routing, item=",item)
                #if not suspend_further_routing:
                if debug:
                    print('--- asset_action calls route_asset')        
                self.route_asset(asset,suspend_further_routing=item['suspend_further_routing'])
            else:
                print("no routing: auto_route=%s, validated=%s"%(str(sr.properties.get('auto_route',False)),str(is_validated)))

        if debug:
            print('<<< asset_action end')
    def flush(self):
        for asset in Asset.objects.filter(stationinroute__route=self):
            self.asset_action(asset)

    #returns a list of possible routes with is_validated indicating a route with fulfilled requirements


    def _check_atomic_requirement(self, asset, requirement):
        payload = asset.payload
        debug = False
        if asset.meta.get('debug',False):
            debug = True
        if debug:
            print(">",self,requirement)
        result = dict()
        result['is_validated'] = False
        result['title'] = requirement['title']
        if requirement['sysname'] == 'STATION_FORMFILL':
            if debug:
                print('station_formfill: checking required fields')
            try:
                if debug:
                    print("checking asset.payload for required_fields presence")
                    print('asset.payload:',asset.payload)
                    print('required_fields:',asset.stationinroute.station.properties['field_templates'][asset.type.sysname].get('required_fields',[]))
                
                result['is_validated'] = True
                for key in asset.stationinroute.station.properties['field_templates'][asset.type.sysname].get('required_fields',[]):
                    if '.' not in key:
                        if debug:
                            print('checking',key)
                        if key not in payload:
                            if debug:
                                print('not in payload, return false')
                            result['is_validated'] = False
                            return result
                        if debug:
                            print('in payload, keep on checking')
                    else:#compound requirement
                        master = key.split('.')[0]
                        subkey = key.split('.')[1]
                        if debug:
                            print('checking compound',key)
                        if master not in payload:
                            if debug:
                                print('not in payload, return false')
                            result['is_validated'] = False
                            return result
                        else:
                            for item in payload[master]:
                                if subkey not in item or item[subkey].strip() == '':
                                    if debug:
                                        print('subkey is not in payload or empty, return false')
                                    result['is_validated'] = False
                        if debug:
                            print('in payload, keep on checking')

                        
            except Exception as e:
                print("ERROR checking sttion_formfill requirement",e)

            if debug:    
                print("returning",result)
            return result
        if '.' in requirement['sysname']: #dictionary requirement 
            if debug:
                print('requirement is dictionary:',requirement['sysname'])

            payload_key_name = requirement['sysname'].split('.')[0]
            dictionary_key_name = requirement['sysname'].split('.')[1]
            if payload_key_name not in payload:
                if debug:
                    print('field',payload_key_name,'not in payload')
                result['is_validated'] = False

            else:
                for dict_value in payload[payload_key_name]:
                    if isinstance(dict_value,dict):
                        if debug:
                            print('payload value is a dict, ok')

                        if dictionary_key_name in dict_value:
                            if debug:
                                print('subkey',dictionary_key_name,' found in value, ok')

                            if 'value_equals' in requirement:
                                if debug:
                                    print('requirement is value_equals')
                                if isinstance(dict_value[dictionary_key_name],str) and isinstance(requirement['value_equals'],str):
                                    if debug:
                                        print('requirement value is string and  is to be compared respectively, current result is',str(result['is_validated']))

                                    #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                                    if dict_value[dictionary_key_name].strip().lower() == requirement['value_equals'].strip().lower():
                                        if debug:
                                            print('comparison success:',dict_value[dictionary_key_name].strip().lower(),'=',requirement['value_equals'].strip().lower())

                                        result['is_validated'] = True
                                else:
                                    if debug:
                                        print('requirement value is not a string')

                                    if dict_value[dictionary_key_name] == requirement['value_equals']:
                                        if debug:
                                            print('comparison success:',str(dict_value[dictionary_key_name]),'=',str(requirement['value_equals']))

                                        result['is_validated'] = True
                            elif 'value_equals_any' in requirement:
                                if debug:
                                    print('requirement is value_equals_any')
                                for requirement_variant in requirement['value_equals_any']:
                                    if isinstance(dict_value[dictionary_key_name],str) and isinstance(requirement_variant,str):
                                        if debug:
                                            print('requirement value is string and  is to be compared respectively, current result is',str(result['is_validated']))

                                        #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                                        if dict_value[dictionary_key_name].strip().lower() == requirement_variant.strip().lower():
                                            if debug:
                                                print('comparison success:',dict_value[dictionary_key_name].strip().lower(),'=',requirement_variant.strip().lower())

                                            result['is_validated'] = True
                                            break
                                    else:
                                        if debug:
                                            print('requirement value is not a string')

                                        if dict_value[dictionary_key_name] == requirement_variant:
                                            if debug:
                                                print('comparison success:',str(dict_value[dictionary_key_name]),'=',str(requirement_variant))

                                            result['is_validated'] = True
                                            break
                            elif 'value_not_equals' in requirement:
                                if debug:
                                    print('requirement is value_not_equals')
                                if isinstance(dict_value[dictionary_key_name],str) and isinstance(requirement['value_not_equals'],str):
                                    #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                                    if dict_value[dictionary_key_name].strip().lower() != requirement['value_not_equals'].strip().lower():
                                        result['is_validated'] = True
                                else:
                                    if dict_value[dictionary_key_name] != requirement['value_not_equals']:
                                        result['is_validated'] = True


                            elif 'value_equals_payload_value' in requirement:
                                if debug:
                                    print('requirement is value_equals_payload_value')
                                #comparing only first items
                                if '.' in requirement['value_equals_payload_value']:
                                    payload_key = requirement['value_equals_payload_value'].split('.')[0]
                                    payload_subkey = requirement['value_equals_payload_value'].split('.')[1]
                                    value_to_compare = None
                                    try:
                                        value_to_compare = payload[payload_key][0][payload_subkey]
                                    except:
                                        value_to_compare = None
                                else:
                                    value_to_compare = None
                                    try:
                                        value_to_compare = payload[requirement['value_equals_payload_value']][0]
                                    except:
                                        value_to_compare = None
        

                                if isinstance(dict_value[dictionary_key_name],str) and isinstance(value_to_compare,str):
                                    #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                                    if dict_value[dictionary_key_name].strip().lower() == value_to_compare.strip().lower():
                                        result['is_validated'] = True
                                else:
                                    if dict_value[dictionary_key_name] == value_to_compare:
                                        result['is_validated'] = True



                            elif 'value_greater' in requirement:
                                if debug:
                                    print('requirement is value_greater')
                                pass
                            elif 'value_less' in requirement:
                                if debug:
                                    print('requirement is value_less')
                                pass
                            elif 'item_count_equals' in requirement:
                                if debug:
                                    print('requirement is item_count_equals')
                                #print("compared len(payload[",requirement['sysname'],'])=',len(payload[requirement['sysname']]),'= requirement[compare_value]=',requirement['compare_value'])
                                if len(payload[requirement['sysname']]) == requirement['item_count_equals']:
                                    result['is_validated']=True
                            elif 'item_count_greater' in requirement:
                                if debug:
                                    print('requirement is item_count_greater')
                                #print("compared len(payload[",requirement['sysname'],'])=',len(payload[requirement['sysname']]),'> requirement[compare_value]=',requirement['compare_value'])
                                if len(payload[requirement['sysname']]) > requirement['item_count_greater']:
                                    result['is_validated']=True
                            elif 'item_count_less' in requirement:
                                if debug:
                                    print('requirement is item_count_less')
                                #print("compared len(payload[",requirement['sysname'],'])=',len(payload[requirement['sysname']]),'< requirement[compare_value]=',requirement['compare_value'])
                                if len(payload[requirement['sysname']]) < requirement['item_count_less']:
                                    result['is_validated']=True
                            else: # trivial presence in payload is checked
                                if debug:
                                    print('requirement is plain presence')
                                result['is_validated'] = True
                        
            
        else: # non-dictionary requirement
            #print("non-dictionary requirement")
            if 'value_equals' in requirement:
                #print('value_equals')
                if requirement['sysname'] in payload:
                    for item in payload[requirement['sysname']]:
                        if isinstance(item,str) and isinstance(requirement['value_equals'],str):
                            #print('{{{{{string comparison:',requirement['sysname'],requirement['value_equals'],'}}}}}')
                            #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                            if item.strip().lower() == requirement['value_equals'].strip().lower():
                                result['is_validated'] = True
                        else:
                            print('{{{{{non-string comparison:',requirement['sysname'],'(',item,')',requirement['value_equals'],'}}}}}')
                            if item == requirement['value_equals']:
                                #print('{{{{{',item,'=',requirement['value_equals'],'}}}}}')
                                result['is_validated'] = True
            elif 'value_equals_any' in requirement:
                #print('value_equals')
                if requirement['sysname'] in payload:
                    for item in payload[requirement['sysname']]:
                        for requirement_variant in requirement['value_equals_any']:
                            if isinstance(item,str) and isinstance(requirement_variant,str):
                                #print('{{{{{string comparison:',requirement['sysname'],requirement['value_equals'],'}}}}}')
                                #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                                if item.strip().lower() == requirement_variant.strip().lower():
                                    result['is_validated'] = True
                                    break
                            else:
                                print('{{{{{non-string comparison:',requirement['sysname'],'(',item,')',requirement['value_equals'],'}}}}}')
                                if item == requirement_variant:
                                    #print('{{{{{',item,'=',requirement['value_equals'],'}}}}}')
                                    result['is_validated'] = True
                                    break
            elif 'value_absent' in requirement:
                #print('value_absent')
                if requirement['sysname'] not in payload:
                    result['is_validated'] = True

            elif 'value_not_equals' in requirement:
                #print('value_equals')
                if requirement['sysname'] in payload:
                    for item in payload[requirement['sysname']]:
                        if isinstance(item,str) and isinstance(requirement['value_not_equals'],str):
                            #print('{{{{{string comparison:',requirement['sysname'],requirement['value_equals'],'}}}}}')
                            #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                            if item.strip().lower() != requirement['value_not_equals'].strip().lower():
                                result['is_validated'] = True
                        else:
                            #print('{{{{{non-string comparison:',requirement['sysname'],requirement['value_equals'],'}}}}}')
                            if item != requirement['value_not_equals']:
                                result['is_validated'] = True
                else:
                    result['is_validated'] = True
                                
            elif 'value_equals_payload_value' in requirement:
                #print('value_equals_payload_value')
                #comparing only first items
                if '.' in requirement['value_equals_payload_value']:
                    payload_key = requirement['value_equals_payload_value'].split('.')[0]
                    payload_subkey = requirement['value_equals_payload_value'].split('.')[1]
                    value_to_compare = None
                    try:
                        value_to_compare = payload[payload_key][0][payload_subkey]
                    except:
                        value_to_compare = None
                else:
                    value_to_compare = None
                    try:
                        value_to_compare = payload[requirement['value_equals_payload_value']][0]
                    except:
                        value_to_compare = None
        
                item = payload[requirement['sysname']][0]
                if isinstance(item,str) and isinstance(value_to_compare,str):
                    #string comparison, must be held case-insensitive and without trailing/leading whitespaces
                    if item.strip().lower() == value_to_compare.strip().lower():
                        result['is_validated'] = True
                else:
                    if item == value_to_compare:
                        result['is_validated'] = True


            elif 'value_greater' in requirement:
                pass
            elif 'value_less' in requirement:
                pass
            elif 'item_count_equals' in requirement:
                if len(payload[requirement['sysname']]) == requirement['item_count_equals']:
                    result['is_validated']=True
            elif 'item_count_greater' in requirement:
                #print(".compared len(payload[",requirement['sysname'],'])=',len(payload[requirement['sysname']]),'and requirement[compare_value]=',requirement['compare_value'])
                if len(payload[requirement['sysname']]) > requirement['item_count_greater']:
                    result['is_validated']=True
            elif 'item_count_less' in requirement:
                if len(payload[requirement['sysname']]) < requirement['item_count_less']:
                    result['is_validated']=True
            else: # trivial presence in payload is checked
                if requirement['sysname'] in payload:
                    result['is_validated'] = True

            #print("_check atomic_requirement returns ",result)
        if debug:
            print('check result is ',str(result))
        return result
        
    def _check_tuple_requirement(self,asset, requirement):
        bad_result = dict()
        bad_result['is_validated'] = False
        bad_result['title'] = requirement['title']

        for requirement_variant in requirement['sysname']:
            mock_requirement = dict()
            mock_requirement['sysname'] = requirement_variant
            for key in requirement:
                if key != 'sysname':
                    mock_requirement[key] = requirement[key]
            variant_result = self._check_atomic_requirement(asset, mock_requirement)
            if variant_result['is_validated']:
                #any variant validated means the whole tuple requirement is validated
                return variant_result
        
        #false result by default
        return bad_result

    def _check_requirement(self, asset, requirement):
        if isinstance(requirement['sysname'],tuple) or isinstance(requirement['sysname'],list):
            return self._check_tuple_requirement(asset, requirement)
        else:
            return self._check_atomic_requirement(asset, requirement)

    def _check_route_variant(self, asset, route_variant):
        route_result = dict()
        route_result['destination_id'] = route_variant['destination_id']
        route_result['auto_route'] = route_variant['auto_route']
        route_result['requirements'] = list()
        if 'payload_modifications' in route_variant:
            route_result['payload_modifications'] = route_variant['payload_modifications']
        if 'asset_type_modifications' in route_variant:
            route_result['asset_type_modifications'] = route_variant['asset_type_modifications']

        total_requirements = 0
        fulfilled_requirements = 0

        for requirement in route_variant.get('requirements',list()):
            #print("about to check requirement",requirement)
            requirement_check_result = self._check_requirement(asset, requirement)
            route_result['requirements'].append(requirement_check_result)
            total_requirements+=1
            if requirement_check_result['is_validated'] == True:
                fulfilled_requirements+=1

        if total_requirements == fulfilled_requirements:
            route_result['is_validated'] = True
        else:
            route_result['is_validated'] = False
        
        return route_result

    def check_routing_requirements(self,asset):
        debug=False
        if debug:
            print("route.check_routing_requirements: checking routing requirements for asset #%i" % asset.pk)
        result = list()
        
        sr = asset.stationinroute
        if 'routing' not in sr.properties:
            if debug:
                print("no routing described in station properties, exiting")
            return result

        for route in sr.properties['routing']:
            if debug:
                print("checking ",route)
            route_variant = self._check_route_variant(asset, route)
            route_variant['auto_route'] = route.get('auto_route',False)
            route_variant['suspend_further_routing'] = route.get('suspend_further_routing',False)

            if 'route_notifications' in route:
                route_variant['route_notifications'] = route['route_notifications']

            if route_variant['destination_id'] == '#RETURN#':
                try:
                    dest_ = RouteRecord.objects.filter(asset=asset)[0].stationinroute.pk
                except:
                    print('Error during getting destination_id from #RETURN#')
                    return
            else:
                dest_ = route['destination_id']


            if sr.station.properties.get('force_return',False) and StationInRoute.objects.get(pk=dest_) != sr.pk:
                route_variant['is_validated'] = False
            result.append(route_variant)
        
        return result

    def process_notifications(self,route_record,debug=False):
        from nexus.input_processing import put_asset_variables_into_string
        from hub_messages.models import Hub_message

        if debug:
            print('process_notifications(self,route_record)')
            print('route_record:',route_record,route_record.properties)

        if not 'notifications' in route_record.properties:
            route_record.properties['notifications'] = list()
        
        prepared_notifications = list()
        #sr->station notifications
        for notification in route_record.stationinroute.station.properties.get('notifications',list()):
            if notification.get('timing','NOT_SET') == 'after':
                prepared_notifications.append(notification)
        #next_sr->station notifications
        for notification in route_record.next_stationinroute.station.properties.get('notifications',list()):
            if notification.get('timing','NOT_SET') == 'before':
                prepared_notifications.append(notification)
        #next_sr notifications
        for notification in route_record.properties.get('route_notifications',list()):
            prepared_notifications.append(notification)


        #for notification in route_record.stationinroute.station.properties['notifications']:
        for notification in prepared_notifications:
            if debug:
                try:
                    print("NOTIFICATION for Asset #",str(route_record.asset.pk),", route_record #",str(route_record.pk),", timing: ",notification['timing'])
                except:
                    pass

            notification_record = dict()
            notification_record['status'] = "ERR"
            try:

                if 'message_template' in notification:
                    if debug:
                        print('message_template notification:',notification)
                        print('ADDRESS:',notification.get('address',''))
                        print('ROUTE RECORD:',route_record.pk,route_record)
                        print('ASSET:',route_record.asset.pk,route_record.asset)
                    message_template = notification['message_template']
                    addresses_list = put_asset_variables_into_string(notification.get('address',''),route_record.asset)
                    if debug:
                        print('ADDRESS STUFFED:',addresses_list)
                    user = route_record.operator
                    asset = route_record.asset
                    if debug:
                        print('about to post_message_from_template')
                    for address in addresses_list.split(','):
                        if address != '':
                            rslt = Hub_message.post_message_from_template(message_template,user,asset,address,route_record)
                    if debug:
                        print('...done')

                    continue

                if not 'type' in notification:
                    continue
                title = put_asset_variables_into_string(notification.get('title','HUB.SFEDU.RU - извещение'),route_record.asset)
                message = put_asset_variables_into_string(notification.get('message',''),route_record.asset)
                recipient = notification.get('recipient','creator')
                attachment = None
                if 'attachment' in notification:
                    if debug:
                        print(notification['attachment'])
                if 'attachment' in notification :
                    if isinstance(notification['attachment'],str) and notification['attachment'] in route_record.asset.payload:
                        #payload key name
                        files = route_record.asset.payload[notification['attachment']]
                        for file_ in files:
                            file_path_full = FileStorage.objects.get(pk=int(file_['storage'])).path+str(route_record.asset.pk)+'/'+file_['uuid']+'/'+file_['filename']
                            file_['filepath'] = file_path_full

                    else:
                        #{"filename","filepath"} dict
                        files = notification['attachment']

                    attachment = files
                    if debug:
                        print(attachment)
                    #attachment = None
                
                dsn = notification.get('dsn',True)
                if not 'address' in notification:
                    continue

                addresses = put_asset_variables_into_string(notification['address'],route_record.asset).split(',')
                for address in addresses:
                    _addr = address.strip()
                    if notification['type'] == 'email':
                        if route_record.asset.meta.get('debug',False):
                            _addr = 'abogomolov@sfedu.ru'

                        if attachment:
                            nexus_sendmail({"to":_addr,"subject":title,"body":message,"attachment":attachment,"DSN":dsn})
                        else:
                            nexus_sendmail({"to":_addr,"subject":title,"body":message,"DSN":dsn})
                     
                #notification_type - email, phone, web
                #notification_title - text with variables
                #notification_message - text with variables
                #notification_address - email address, phone number and so on
                
                notification_record['status'] = "OK"
                notification_record['type'] = notification['type']
                notification_record['title'] = title
                notification_record['address'] = addresses
                notification_record['message'] = message
                notification_record['attachment'] = attachment
                notification_record['dsn'] = dsn
                notification_record['recipient'] = recipient
            except BaseException as e:
                print("ERROR during notification (",route_record,"):",e)

                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                print(exc_type, fname, exc_tb.tb_lineno)

                #continue
            route_record.properties['notifications'].append(notification_record)
            route_record.save()

    def process_asset_type_modifications(self,route_variant, asset):
        if 'asset_type_modifications' not in route_variant:
            return

        print("asset_type modifications start for ",asset.pk)
        print("initial asset type is",asset.type.pk)
        for modification in route_variant['asset_type_modifications']:
            if '->' in modification:
                type_names = modification.split('->')
                old_type = type_names[0]
                new_type = type_names[1]
                if old_type == '*':
                    try:
                        new_type_obj = AssetType.objects.get(pk=int(new_type))
                        asset.type=new_type_obj
                    except:
                        pass
                else:
                    if int(old_type) == asset.type.pk:
                        try:
                            new_type_obj = AssetType.objects.get(pk=int(new_type))
                            asset.type=new_type_obj
                        except:
                            pass

        asset.save()
        print("resulting asset type is",asset.type.pk)

    def process_payload_modifications(self,route_variant, asset):
        "payload_modifications(list) - list of payload variables' names that should be manipulated before next station arrival<br>"
        "    +<variable_name>=[<string>|BOOL_<val>|INT_<val>|DATETIME_NOW|DATETIME_NOW_FORMATTED|ASSET_ID] means that the variable must be added<br>"
        "    -<variable_name> means that the variable must be deleted if exists<br>"
        "    <variable_name_1-><variable_name_2> means the variable 1 should be renamed to variable 2<br>"
        "    <variable_name_1->#META#<variable_name_2> means the variable 1 should be moved to meta variable 2<br>"
        "    #META#<variable_name_1-><variable_name_2> means the meta variable 1 should be moved to payload variable 2<br>"
        "    <variable_name_1+><variable_name_2> means the variable 1 should be copied to variable 2, 1.2->3 means that subkey 2 should be copied to variable 3<br>"
        "    #META_SINGULARISE#variable converts a list meta value to a single value"
        "    #META_PLURALISE#variable converts a single meta value to a list value"
        "    <variable_name_1~><variable_name_2>#FORMAT:[%B%N%D%X%U%] means the variable 1 should be appended to variable 2 as text;"
        "   #%X means variable_1 must be deleted, "
        "   #%U means username must be added, "
        "   #%D means datetime must be added; "
        "   #%VAR means variable_1; "
        "   any other text gets inserted as-is.<br>"
        "   var#INCREASE, var#DECREASE increases or decreases numeric var value respectively<br>"
        "   var#CREATE creates text variable var and fills it with whatever is after 'CREATE:':  author#CREATE:some variable value<br>"
        "   var#CREATE exits if encounters existing value"
        if 'payload_modifications' not in route_variant:
            return
        debug = False
        if asset.meta.get('debug',False):
            debug = True
        if debug:
            print("payload modifications start for ",asset.pk)
        for modification in route_variant['payload_modifications']:
            if debug:
                print(modification)
            if modification.startswith('+'):
                if debug:
                    print("modification starts with +:",modification)
                key = modification[1:].split('=')[0]
                if debug:
                    print("key:",key)
                try:
                    value_serialized = modification.split('+'+key+'=',1)[1]
                    if debug:
                        print("value:",value_serialized)

                    if value_serialized.startswith('#META#'):
                        if debug:
                            print('#META# case, looking for ',value_serialized.replace('#META#','').strip(),'in meta')
                        if value_serialized.replace('#META#','').strip() in asset.meta:
                            value = asset.meta[value_serialized.replace('#META#','').strip()]
                            if debug:
                                print('found, value = ',value)
                        else:
                            if debug:
                                print('value is absent in meta, meta keys:',str(asset.meta.keys()))

                    elif value_serialized.startswith('BOOL_'):
                        if value_serialized.replace('BOOL_','').strip() in ['True','true']:
                            value = True
                        if value_serialized.replace('BOOL_','').strip() in ['False','false']:
                            value = False
                    elif value_serialized.startswith('INT_'):
                        value = ast.literal_eval(value_serialized.replace('BOOL_','').strip())
                    elif value_serialized == 'DATETIME_NOW':
                        value = str(datetime.datetime.now())
                    elif value_serialized == 'ASSET_ID':
                        value = asset.pk
                        if debug:
                            print("ASSET_ID:",value)
                    elif value_serialized == 'DATETIME_NOW_FORMATTED':
                        value = datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
                    else:#str
                        value = value_serialized
                    
                    if key in asset.payload:
                        asset.payload[key].append(value)
                    else:
                        asset.payload[key] = [value]
                except:
                    continue
            if modification.startswith('-'):
                key = modification[1:]
                if key not in asset.payload:
                    continue
                try:
                    mf = MasterField.objects.get(sysname=key)
                    if not mf.properties.get('field_compound',False):
                        if mf.properties.get('is_filefield',False):
                            for value in asset.payload[key]:
                                file_dict = value
                                file_path = FileStorage.objects.get(pk=int(file_dict['storage'])).path+str(asset.pk)+'/'+file_dict['uuid']
                                #logger.debug(file_path)
                                try:
                                    shutil.rmtree(file_path)
                                    #logger.debug('deleted')
                                except:
                                    #logger.debug('error deleting file')
                                    pass

                    else:
                        #compound field
                        for dictvalue in asset.payload[key]:
                            for subkey in dictvalue:
                                try:
                                    sub_mf = MasterField.objects.get(sysname=subkey)
                                    if sub_mf.properties.get('is_filefield',False):
                                        file_dict = dictvalue[subkey]
                                        file_path = FileStorage.objects.get(pk=int(file_dict['storage'])).path+str(asset.pk)+'/'+file_dict['uuid']
                                        try:
                                            shutil.rmtree(file_path)
                                        except:
                                            pass

                                except:
                                    pass

                except:
                    pass
                dump = asset.payload.pop(key,None)
                #print("done")
                continue
            if modification.startswith('#META_SINGULARISE#'):
                key=modification.replace('#META_SINGULARISE#','')
                if key not in asset.meta:
                    continue
                if not isinstance(asset.meta[key],list):
                    continue
                if len(asset.meta[key])<1:
                    continue
                asset.meta[key]=asset.meta[key][0]    
                continue
            if modification.startswith('#META_PLURALISE#'):
                if debug:
                    print('#META_PLURALISE#')
                key=modification.replace('#META_PLURALISE#','')
                if key not in asset.meta:
                    continue
                if isinstance(asset.meta[key],list):
                    continue
                asset.meta[key]=[asset.meta[key]]    
                continue
            if '->' in modification:
                if debug:
                    print('-> modification:',modification)
                key_names = modification.split('->')
                old_key = key_names[0]
                new_key = key_names[1]
                from_meta=False
                if old_key.startswith('#META#'):
                    old_key=old_key.replace('#META#','')
                    from_meta=True
                to_meta=False
                if new_key.startswith('#META#'):
                    new_key=new_key.replace('#META#','')
                    to_meta=True
                if debug:
                    print("from_meta:",from_meta,",to_meta:",to_meta)
                    
                if from_meta:
                    if old_key not in asset.meta:
                        continue
                else:
                    if old_key not in asset.payload:
                        continue
                        
                if '.' not in new_key:
                    if from_meta and to_meta:
                        asset.meta[new_key] = asset.meta.pop(old_key)
                    elif from_meta:
                        asset.payload[new_key] = asset.meta.pop(old_key)
                    elif to_meta:
                        asset.meta[new_key] = asset.payload.pop(old_key)
                    else:
                        asset.payload[new_key] = asset.payload.pop(old_key)
                else:
                    if debug:
                        print('"." encountered in new_key:',new_key,'(modification:',modification,',old_key:',old_key,')')
                    key_parts = new_key.split('.')
                    new_key = key_parts[0]
                    new_subkey = key_parts[1]
                    if debug:
                        print('key:',new_key,',subkey:',new_subkey)
                    values_list=list()
                    if from_meta:
                        for value in asset.meta[old_key]:
                            new_item = {}
                            new_item[new_subkey] = value
                            values_list.append(new_item)
                    else:
                        for value in asset.payload[old_key]:
                            new_item = {}
                            new_item[new_subkey] = value
                            values_list.append(new_item)
                    if debug:
                        print('prepared values_list:',values_list)
                    
                    if to_meta:
                        asset.meta[new_key] = values_list
                    else:
                        asset.payload[new_key] = values_list
                    if debug:
                        print('set',new_key,' with values_list')
                    if from_meta:
                        dump = asset.meta.pop(old_key,None)
                    else:
                        dump = asset.payload.pop(old_key,None)
                    if debug:
                        print('removed old_key')
                    
                #print("done")
                continue
            if '~>' in modification:
                key_names = modification.split('#FORMAT:')[0].split('~>')
                old_key = key_names[0]
                new_key = key_names[1]
                if old_key not in asset.payload:
                    continue
                if new_key not in asset.payload:
                    asset.payload[new_key] = [""]
                if len(asset.payload[new_key]) < 1:
                    continue
                if not isinstance(asset.payload[new_key][-1],str):
                    continue

                result_str = asset.payload[new_key][-1]
                
                try:
                    str_format = modification.split('#FORMAT:')[1]
                except:
                    str_format = ""
                #XNBUD VAR
                try:
                    username = asset.operator.username
                except:
                    username = ""

                current_datetime = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

                for item in asset.payload[old_key]:
                    new_str = str_format
                    new_str = new_str.replace('%U',username)
                    new_str = new_str.replace('%D',current_datetime)
                    new_str = new_str.replace('%VAR',str(item))
                    new_str = new_str.replace('%X','')
                    result_str+=new_str
                    
                asset.payload[new_key][-1] = result_str
                if '%X' in str_format:
                    dump = asset.payload.pop(old_key, None)
                    
                continue
            if '+>' in modification:
                key_names = modification.split('+>')
                old_key = key_names[0]
                new_key = key_names[1]
                if '.' in old_key:
                    key_parts = old_key.split('.')
                    old_key = key_parts[0]
                    old_subkey = key_parts[1]
                else:
                    old_subkey = None

                if old_key not in asset.payload:
                    continue
                if not old_subkey:
                    #non-nested value, copy whole
                    asset.payload[new_key] = asset.payload[old_key]
                else:
                    #nested value, copy it
                    values_list = list()
                    for value in asset.payload[old_key]:
                        values_list.append(value[old_subkey])

                    asset.payload[new_key] = values_list
                continue
            if '#DECREASE' in modification:
                var_name = modification.split('#')[0]
                if var_name not in asset.payload:
                    continue
                modified_values = list()
                for value in asset.payload[var_name]:
                    if isinstance(value,int):
                        modified_values.append(value-1)
                    else:
                        modified_values.append(value)
                asset.payload[var_name] = modified_values
                continue
            if '#INCREASE' in modification:
                var_name = modification.split('#')[0]
                if var_name not in asset.payload:
                    continue
                modified_values = list()
                for value in asset.payload[var_name]:
                    if isinstance(value,int):
                        modified_values.append(value+1)
                    else:
                        modified_values.append(value)
                asset.payload[var_name] = modified_values


                #print("done")
                continue
            if '#CREATE:' in modification:
                var_name = modification.split('#')[0]
                var_value = modification.split('#CREATE:')[1]
                if var_name in asset.payload:
                    continue

                asset.payload[var_name] = [var_value]


                #print("done")
                continue
        asset.save()


    def route_asset(self,asset,destination_id=None,suspend_further_routing=False):
        #print(">route.route_asset: routing asset #",asset.pk,"to destination_id=",destination_id)
        debug=False
        if asset.meta.get('debug',False):
            debug = True

        if debug:
            print('>>> route_asset(asset=',asset,',destination_id=',destination_id,')')

        result = dict()
        result['status'] = 400
        result['context'] = None
        result['previous_stationinroute_id'] = asset.stationinroute.pk
        result['previous_station_id'] = asset.stationinroute.station.pk
        
        sr = asset.stationinroute
        check_result = self.check_routing_requirements(asset)
        is_validated = False
        for item in check_result:
            if debug:
                print("checking routing variant",item)
            if item['is_validated']:
                if not suspend_further_routing:
                    _suspend_further_routing = item.get('suspend_further_routing',False)
                else:
                    _suspend_further_routing = True
                if _suspend_further_routing:
                    if debug:
                        print('FURTHER ROUTING SUSPENDED:',item)
                #print("route to %s is validated" % str(item['destination_id']))
                if debug:
                    print("Routing to sr #",item['destination_id'],", all requirements are ok:",item['requirements'])
                if destination_id:
                    if debug:
                        print("destination_id = ",destination_id)
                    if destination_id == '#RETURN#':
                        try:
                            dest_ = RouteRecord.objects.filter(asset=asset)[0].stationinroute.pk
                        except:
                            print('Error during getting destination_id from #RETURN#')
                            if debug:
                                print('<<< end route_asset')
                            return
                    else:
                        dest_ = destination_id
                    if str(destination_id) == str(item['destination_id']):
                        #print("route leads to target destination",str(destination_id))
                        next_sr = StationInRoute.objects.get(pk=item['destination_id'])
                        asset.stationinroute = next_sr
                        asset.save()

                        self.process_payload_modifications(item, asset)
                        self.process_asset_type_modifications(item, asset)


                        """
                            same-station routing does not create route_record and station notifications 
                            also it does not assign asset
                        """
                        if sr != next_sr:

                            #===
                            record = RouteRecord()
                            record.route = self
                            record.stationinroute = sr
                            record.next_stationinroute = next_sr
                            record.asset = asset
                            if sr.station.classname=='Station': #human-operated station, put operator in record
                                if not asset.operator:
                                    if sr.station.properties.get('creator_operator',False) or (sr.properties.get('allow_adding_assets',False) and sr.station.properties.get('non_operator_adding_assets',False)):
                                        try:
                                            record.operator = User.objects.get(pk=asset.meta['creator'])
                                        except:
                                            record.operator = None
                                    else:
                                        record.operator = None
                                else:
                                    record.operator = asset.operator
                            else:
                                record.operator = None
                            #print("operator:",record.operator)
                            record.is_a_rewind = False
      
                            if 'route_notifications' in item:
                                record.properties['route_notifications'] = item['route_notifications']
                                if debug:
                                    print('ROUTE NOTIFICATIONS ARE PRESENT')
                                    print(item['route_notifications'])
                            else:
                                if debug:
                                    print('ROUTE NOTIFICATIONS ARE ABSENT')
                            
                            record.save()
                            if debug:
                                print('--- route_asset calls assign_asset')

                            next_sr.station.assign_asset(asset,suspend_further_routing=_suspend_further_routing)
                            if debug:
                                print('--- route_asset calls process_notifications')
                          
                            self.process_notifications(record,debug=debug)




                        result['status'] = 200
                        result['stationinroute_id'] = next_sr.pk
                        result['route_id'] = next_sr.route.pk
                        result['asset_id'] = asset.pk
                        result['station_id'] = asset.stationinroute.station.pk

                        if debug:
                            print('<<< end route_asset')
                        return result
                else:
                    if debug:
                        print('no destination_id provided')
                    if not item['auto_route']:
                        #if no destination_id specified, omit routing to non-auto_route destinations
                        continue

                    if item['destination_id'] == '#RETURN#':
                        try:
                            dest_ = RouteRecord.objects.filter(asset=asset)[0].stationinroute.pk
                        except:
                            print('Error during getting destination_id from #RETURN#')
                            return
                    else:
                        dest_ = item['destination_id']

                    next_sr = StationInRoute.objects.get(pk=dest_)
                    asset.stationinroute = next_sr
                    asset.save()

                    self.process_payload_modifications(item, asset)
                    self.process_asset_type_modifications(item, asset)

                    """
                        same-station routing does not create route_record and station notifications 
                        also it does not assign asset
                    """
                    if sr != next_sr:
                        record = RouteRecord()
                        record.route = self
                        record.stationinroute = sr
                        record.next_stationinroute = next_sr
                        record.asset = asset
                        if sr.station.classname=='Station': #human-operated station, put operator in record
                            if not asset.operator:
                                if sr.station.properties.get('creator_operator',False) or (sr.properties.get('allow_adding_assets',False) and sr.station.properties.get('non_operator_adding_assets',False)):
                                    try:
                                        record.operator = User.objects.get(pk=asset.meta['creator'])
                                    except:
                                        record.operator = None
                                else:
                                    record.operator = None
                            else:
                                record.operator = asset.operator
                        else:
                            record.operator = None

                        #print("operator:",record.operator)

                        record.is_a_rewind = False
                        if 'route_notifications' in item:
                            record.properties['route_notifications'] = item['route_notifications']
                            if debug:
                                print('ROUTE NOTIFICATIONS ARE PRESENT')
                                print(item['route_notifications'])
                        else:
                            if debug:
                                print('ROUTE NOTIFICATIONS ARE ABSENT')

                        record.save()
                        if debug:
                            print('--- route_asset calls assign_asset')
                        next_sr.station.assign_asset(asset,suspend_further_routing=_suspend_further_routing)
                        if debug:
                            print('--- route_asset calls process_notifications')

                        self.process_notifications(record)

                    #next_sr.station.assign_asset(asset)
                    result['status'] = 200
                    result['stationinroute_id'] = next_sr.pk
                    result['asset_id'] = asset.pk
                    result['route_id'] = next_sr.route.pk
                    result['station_id'] = asset.stationinroute.station.pk

                    if debug:
                        print('<<< end route_asset')

                    return result


        result['message'] = "Routing did not validate"
        return result

    def rewind_asset(self,asset):
        print("route.rewind_asset: routing asset #%i" % asset.pk)
        if hasattr(self,'route_stations'):
            for route_station in self.route_stations.all():
                if route_station == asset.stationinroute:
                    if route_station.can_route_back:
                        try:
                            sr=asset.stationinroute
                            last_record = RouteRecord.objects.filter(route=self).filter(asset=asset).filter(is_a_rewind=False).order_by('-record_datetime')[1]
                            last_record.station.assign_asset(asset,last_record.operator)

                            next_sr = asset.stationinroute
                            record = RouteRecord()
                            record.route = self
                            record.stationinroute = sr
                            record.next_stationinroute = next_sr
                            record.asset = asset

                            if sr.station.classname=='Station': #human-operated station, put operator in record
                                if not asset.operator:
                                    if sr.station.properties.get('creator_operator',False) or (sr.properties.get('allow_adding_assets',False) and sr.station.properties.get('non_operator_adding_assets',False)):
                                        try:
                                            record.operator = User.objects.get(pk=asset.meta['creator'])
                                        except:
                                            record.operator = None
                                    else:
                                        record.operator = None
                                else:
                                    record.operator = asset.operator
                            else:
                                record.operator = None

                            record.is_a_rewind = True
                            record.save()
                            return True
                        except:
                            return False
                    else:
                        return False

            return False
        else:
            return False

    def get_stations_list(self):
        stations = list()
        route = self

        try:
            #station = route.route_stations.all().filter(next_station_in_route=None)[0]


            station = route.route_stations.filter(previous_station__isnull=True).filter(properties__allow_adding_assets=True).first()
        except:
            station = None

        while station:
            #prevent route from adopting stations from other routes - this can happen if route station leads to some other route's station
            if station.route != route:
                break
            #stations.insert(0,station)
            stations.append(station)
            try:
                #station = station.previous_station.all()[0]
                station = station.next_station_in_route
            except:
                station = None

        return stations

    def __str__(self):
        return "" + self.route_name

class StationInRoute(models.Model):
    default_settings = dict()
    default_settings['routing'] = list()
    default_settings['allow_adding_assets'] = False
    station = models.ForeignKey('Station',related_name='station_routes',on_delete=models.CASCADE)
    route = models.ForeignKey('Route',related_name='route_stations',on_delete=models.CASCADE)
    next_station_in_route = models.ForeignKey("self",blank=True,null=True,related_name='previous_station',on_delete=models.SET_NULL)
    can_route_back = models.BooleanField(default=False,)

    properties_help_text=(
        "contains 'routing' list of dicts<br>"
        "first item in list is a default route and is distinguished from others in ui<br>"
        "each list item is a dict with the following keys:<br>"
        "suspend_further_routing(bool) - if true, routing stops and no further routing is done until some action (e.g., update_payload) occurs<br>"
        "destination_id(int) - next StationInRoute<br>"
        "routing to same stationinroute does not create routerecord, doesn't notify anybody and doesn't assign asset, may be used for payload and asset type modifications"
        "route_notifications(list) - just like Station's (see for details), allows to post messages if current routing variant is validated and committed. Values: type(email,sms,web),timing-unusable in context, title, message, address, attachments, DSN, recipient.<br>"
        "auto_route(bool) - whether routing occurs automatically or by operator submit<br>"
        "payload_modifications(list) - list of payload variables' names that should be manipulated before next station arrival<br>"
        "    +[variable_name]=[[string]|BOOL_[val]|INT_[val]|DATETIME_NOW|DATETIME_NOW_FORMATTED|ASSET_ID] means that the variable must be added<br>"
        "    -[variable_name] means that the variable must be deleted if exists<br>"
        "    [variable_name_1-][variable_name_2] means the variable 1 should be renamed to variable 2<br>"
        "    [variable_name_1-]#META#[variable_name_2] means the variable 1 should be moved to meta variable 2<br>"
        "    #META#[variable_name_1-][variable_name_2] means the meta variable 1 should be moved to payload variable 2<br>"
        "    [variable_name_1+][variable_name_2] means the variable 1 should be copied to variable 2, 1.2->3 means that subkey 2 should be copied to variable 3<br>"
        "    #META_SINGULARISE#variable converts a list meta value to single value"
        "    #META_PLULARISE#variable converts a single meta value to list value"
        "    [variable_name_1~][variable_name_2]#FORMAT:[%B%N%D%X%U%] means the variable 1 should be appended to variable 2 as text;<br>"
        "   #%X means variable_1 must be deleted, <br>"
        "   #%U means username must be added, <br>"
        "   #%D means datetime must be added;<br> "
        "   #%VAR means variable_1; <br>"
        "   any other text gets inserted as-is.<br>"
        "   var#INCREASE, var#DECREASE increases or decreases numeric var value respectively<br>"
        "   var#CREATE creates text variable var and fills it with whatever is after 'CREATE:':  author#CREATE:some variable value<br>"
        "   var#CREATE exits if encounters existing value"
        "asset_type_modifications(list) - list of asset type changes, *->12 means that regardless of asset type, it must be changed to 12; 11->12 means that only assets of type 11 should be changed to 12<br>"
        "requirements(list) - list of dicts containing requirements for route advance<br>"
        "    each requirement consists of the following keys:<br>"
        "    sysname(str|tuple) - a name of a value in asset's payload; tuple contains a set of names ANY of which can qualify<br>"
        "    if sysname == STATION_FORMFILL, asset.payload is checked for station's required_fields<<br>"
        "    title(str) - literal name of a requirement for operator<br>"
        "    value_equals(str|int) - #shortcut for compare_function='value_equals' and compare_value<br>"
        "    value_equals_any(list of str|int) - validates if any of the values in the supplied list matches payload value<br>"
        "    value_absent(anything) - if present, sysname must not be in asset payload to validate<br>"
        "    value_not_equals(str|int) - #shortcut for compare_function='value_equals' and compare_value<br>"
        "    #compare_functions - optional arguments that define how requirement accordance is evaluated; default is value_equals (strings are compared stripped and lowercased, other types - as is)<br>"
        "    also supports value_greater, value_less, item_count_equals, item_count_less,item_count_greater, value_equals_payload_value (compares only the first items in value lists)<br>"
        #"     compare_value(str|int) - a value to compare with<br>"
        "also contains allow_adding_assets(bool) whether it allow operator to add assets<br>"
        "asset_message(str) - gets added on top of station edit form, overwrites station's asset_message<br>"
        "also may contain exit_station(bool) - set to true to count route's finished tasks<br>"
    )

    properties = JSONDictField(blank=True,null=True,default = default_settings.copy(),help_text=properties_help_text)

    def __str__(self):
        return "" + self.route.route_name + ", " + self.station.station_name

class RouteRecord(models.Model):
    route = models.ForeignKey('Route',related_name='+',on_delete=models.CASCADE)
    stationinroute = models.ForeignKey('StationInRoute',related_name='+',on_delete=models.CASCADE)
    next_stationinroute = models.ForeignKey('StationInRoute',related_name='+',on_delete=models.CASCADE)
    asset = models.ForeignKey('Asset',related_name='+',on_delete=models.CASCADE)
    operator = models.ForeignKey(User,blank=True,null=True,related_name='+',on_delete=models.SET_NULL)
    record_datetime = models.DateTimeField(default=datetime.datetime.now,)
    is_a_rewind = models.BooleanField(default=False,)
    properties = JSONDictField(default = dict,)

    def __str__(self):
        suffix = ""
        if self.is_a_rewind:
            suffix = " = ВОЗВРАТ = "
        operator_username = u""
        if self.operator:
            operator_username = self.operator.username
        return str(self.pk)+" " + str(self.record_datetime) + " - " + self.stationinroute.station.station_name + ", " + operator_username + suffix

class Station(models.Model):
    default_settings = dict()
    default_settings['auto_assign_mode_on'] = False #True
    default_settings['auto_assign_mode'] = "balanced" # least_busy
    default_settings['same_operator_assign_mode'] = "encourage" #deprecate, carefree
    default_settings['field_templates'] = dict() #deprecate, carefree

    station_name = models.CharField(max_length=255,)
    classname = models.CharField(max_length=255,default="Station")
    operators = models.ManyToManyField(User,blank=True,related_name='nexus_stations',)
    supervisors = models.ManyToManyField(User,blank=True,related_name='nexus_stations_supervised',)
    station_url = models.CharField(max_length=255,blank=True,null=True,)
    properties_help_text = (
        "auto_assign_mode_on(bool) - whether assets are being assigned automatically on arrival, default is False<br>"
        "auto_assign_mode(str) - how operator is being chosen. Can be 'balanced'(default) or 'least_busy'<br>"
        "reassign_on_return(bool) - if asset appears more than once, it gets automatically assigned to the same operator that initially got assigned regardless of auto_assign_mode <br>"
        "same_operator_assign_mode(str) - how to treat previous-station operator: 'encourage'(default) assingnment on him, 'deprecate' or 'carefree' <br>"
        "field_templates(dict): each key represents a template for asset type referenced by sysname; each value is a dict with the following keys:<br>"
        "  readonly_fields(list) - sysnames of masterfields that can be viewed on this station<br>"
        "  required_fields(list) - sysnames of masterfields that must be filled in order to save asset; compound subfields can be required fields<br>"
        "  appendable_fields(list) - sysnames of masterfields that can be appended to asset<br>"
        "  editable_fields(list) - sysnames of masterfields that are printed out and editable during asset edit<br>"
        "field_properties(dict): each key represents field sysname and contains overrides for the following properties as subkeys:<br>"
        "  help_text: override masterfield help_text and assettype help_text<br>"
        "  title: override masterfield title and assettype title<br>"
        "logging(dict): settings for logging<br>"
        "   level(str): 'short', 'full'<br>"
        "default_filestorage(int)<br>"
        "http_station_template(str) - if set, gets rendered instead of station.html when displaying a station<br>"
        "http_asset_template(str) - if set, gets rendered instead of asset.html when displaying asset<br>"
        "non_operator_adding_assets(bool) - whether non-operators can add assets or not, default=False. asset added by non-operator doesn't get assigned automatically<br>"
        "creator_operator(bool) - creator can edit asset, default is False<br>"
        "notify_operator(bool) - whether to notify operator of asset assignment or not, default is False<br>"
        "allow_field_overrides(bool) - if set to True, station editable, appendable, required and readonly are dynamically created from asset.payload['field_overrides].<br>"
        "field_overrides is a comma-separated string of editable fields with modifiers: + means field is also appendable, * means field is also  required, $ means field is also readonly<br>"
        "create_field_overrides(bool) - if set to True, operator can create field_overrides for using elsewhere<br>"
        "a field named field_overrides must be editable on the station in order to create_field_overrides to take effect<br>"
        "description_url(string) - path to html page with station description for operators<br>"
        "asset_message (string) - if present, gets written in add and edit form, on top of usual memo about required fields<br>"
        "force_return(bool) - when set to true, the station always demands that assets should be routed back to previous stationinroute. E.g., this is needed on SplitterStation that creates new assets - without returning to previous stationinroute it would not generate RouteRecords needed to account spawned assets as operator's achievements. Default is False.<br>"
        "notifications(list): if set, contains list of dicts that is used to notify users and operators of route event.<br>"
        "    #type - email, sms, web<br>"
        "    #timing - when the notification occurs - before(applied to stationinroute) or after(applied to next_stationinroute) the routing event<br>"
        "    #title - text with variables, see AssetType's signature_string property for example<br>"
        "    #message - text with variables, see AssetType's signature_string property for example<br>"
        "    #address - email address, phone number and so on<br>"    
        "    #attachments - list of file dictionaries to attach to a letter<br>"    
        "    #DSN(bool) - whether receive delivery status notifications or not<br>"    
        "    #recipient(str) - creator,operator,supervisor. creator can see only his notifications, supervisor sees all, operator can see all except supervisor's<br>"    
    )

    properties = JSONDictField(blank=True,null=True,default = default_settings.copy(),help_text=properties_help_text)


    #must be overridden by child class; gets fired every time station's asset gets saved
    def perform(self,asset):
        #logger.debug('station.perform (classname=%s)'%self.classname)
        if self.classname == "Station":
            print("station.perform: mock perform fired")
        else:
            custom_class = eval(self.classname,globals())
            obj = custom_class()
            obj.perform(asset)



    def flush(self):
        for asset in Asset.objects.filter(stationinroute__station=self):
            self.perform(asset)
    
    def get_field_templates(self, asset_type_sysname,asset_payload=None):
        field_templates = dict()
        if not self.properties.get('allow_field_overrides',False):

            field_templates['editable_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('editable_fields',[])
            field_templates['appendable_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('appendable_fields',[])
            field_templates['readonly_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('readonly_fields',[])
            field_templates['required_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('required_fields',[])

            return field_templates

        else:
            if asset_payload == None or 'field_overrides' not in asset_payload or len(asset_payload['field_overrides']) < 1:
                field_templates['editable_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('editable_fields',[])
                field_templates['appendable_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('appendable_fields',[])
                field_templates['readonly_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('readonly_fields',[])
                field_templates['required_fields'] = self.properties.get('field_templates',{}).get(asset_type_sysname,{}).get('required_fields',[])

                return field_templates
            else:
                """
                    field_overrides:
                    comma-separated list of editable field sysnames with modifiers of the following format: + means appendable field, * means required field, $means readonly field
                """
                field_templates['editable_fields'] = []
                field_templates['appendable_fields'] = []
                field_templates['readonly_fields'] = []
                field_templates['required_fields'] = []

                fld_overrides = asset_payload['field_overrides'][0].split(',')
                for fld in fld_overrides:
                    is_required = False
                    is_appendable = False
                    is_readonly = False

                    if '+' in fld:
                        is_appendable = True
                    if '*' in fld:
                        is_required = True
                    if '$' in fld:
                        is_readonly = True

                    fld_cleaned = fld.replace('+','').replace('*','').replace('$','')

                    field_templates['editable_fields'].append(fld_cleaned)
                    if is_appendable:
                        field_templates['appendable_fields'].append(fld_cleaned)
                    if is_required:
                        field_templates['required_fields'].append(fld_cleaned)
                    if is_readonly:
                        field_templates['readonly_fields'].append(fld_cleaned)

                return field_templates

    def assign_asset(self,asset,rewind_to_operator=None,suspend_further_routing=False):
        debug=False
        if asset.meta.get('debug',False):
            debug=True
        if debug:
            print(">>> station.assign_asset: assigning asset #%i to operator of station #%i" %(asset.pk,self.pk))

        last_assignment = self.properties.get('last_assignment', None)
        print("last assignment: ",last_assignment)
        if last_assignment:
            if self.operators.filter(username=last_assignment).count() == 0:
                print("Invalid last assignment, clearing - ",last_assignment)
                dump = self.properties.pop('last_assignment',None)
                self.save()
        #asset.stationinroute=new_stationinroute
        next_operator = None
        if rewind_to_operator:
            print("rewinded to operator")
            asset.operator = rewind_to_operator
        elif asset.stationinroute.station.properties.get('creator_operator',False) and self.properties['auto_assign_mode_on']:
            #assign creator as operator
            creator = None
            if 'creator' in asset.meta:
                try:
                    creator = User.objects.get(pk=int(asset.meta['creator']))
                except:
                    creator = None
            if 'creator_str' in asset.meta:
                try:
                    creator = User.objects.get(pk=int(asset.meta['creator_str']))
                except:
                    creator = None
            asset.operator = creator
        else:
            reassigned=False
            if self.properties.get('reassign_on_return',False) == True:
                rr = RouteRecord.objects.filter(asset=asset).filter(stationinroute=asset.stationinroute).order_by('-pk')
                if rr.count() > 0:
                    if rr.first().operator:
                        if rr.first().operator in self.operators.all():
                            asset.operator = rr.first().operator
                            reassigned = True


            if self.properties['auto_assign_mode_on'] == True and not reassigned:
                print("station auto assign mode is set to on")
                if self.properties['same_operator_assign_mode'] != "deprecate":
                    print( "same operator assign is not deprecated")
                    if asset.operator in self.operators.all():
                        print("asset operator in station operators, leave intact")
                        next_operator = asset.operator
                        #leave operator intact
                        pass
                    else:
                        #choose operator based on auto_assign_mode
                        if self.properties['auto_assign_mode'] == "balanced":
                            print("auto assign mode = balanced")
                            #sequential assignment - one by one operator
                            last_assignment = self.properties.get('last_assignment', None)
                            if last_assignment:
                                assignment_valid = False
                                for op in self.operators.all():
                                    if op.username == last_assignment:
                                        assignment_valid = True
                                        break
                                if not assignment_valid:
                                    print("last assignment (",last_assignment,") is invalid, clearing")
                                    last_lassignment = None
                            
                            found_operator = False
                            next_operator = None
                            for op in self.operators.all():
                                if op.username == last_assignment:
                                    found_operator = True
                                else:
                                    if found_operator:
                                        next_operator = op
                                        found_operator = False
                                        break
                            if found_operator:
                                if self.operators.count() > 0:
                                    next_operator = self.operators.all()[0]
                            if not last_assignment:
                                if self.operators.count() > 0:
                                    next_operator = self.operators.all()[0]

                            if next_operator:
                                self.properties['last_assignment'] = next_operator.username
                                self.save()
                                asset.operator=next_operator
                                print("operator assigned - "+next_operator.username)
                            else:
                                print("operator is not assigned")

                        if self.properties['auto_assign_mode'] == "least_busy":
                            print("auto assign mode = least_busy")
                            least_count = 0
                            least_busy_operator = None
                            for op in self.operators.all():
                                op_count = self.assets.filter(operator=op).count()
                                if op_count < least_count or not least_busy_operator:
                                    least_busy_operator = op
                                    least_count = op_count
                            next_operator = least_busy_operator
                            asset.operator = least_busy_operator
                else:
                    #choose operator based on auto_assign_mode, not same operator - it is deprecated
                    #print "same operator is deprecated, choose from other operators"
                    if self.properties['auto_assign_mode'] == "balanced":
                        #sequential assignment - one by one operator
                        last_assignment = self.properties.get('last_assignment', None)
                        found_operator = False
                        next_operator = None
                        for op in self.operators.all():
                            if op.username == last_assignment:
                                found_operator = True
                            else:
                                if found_operator and found_operator != asset.operator:
                                    next_operator = op
                                    found_operator = False
                                    break
                        if found_operator:
                            for op in self.operators.all():
                                if op != asset.operator:
                                    next_operator = op

                        if next_operator:
                            self.properties['last_assignment'] = next_operator.username
                            self.save()
                            asset.operator=next_operator

                    if self.properties['auto_assign_mode'] == "least_busy":
                        least_count = 0
                        least_busy_operator = None
                        for op in self.operators.all():
                            if op != asset.operator:
                                op_count = self.assets.filter(operator=op).count()
                                if op_count < least_count or not least_busy_operator:
                                    least_busy_operator = op
                                    least_count = op_count
                        next_operator = least_busy_operator
                        asset.operator = least_busy_operator
            else:
                if not reassigned:
                    #print "station auto assign mode is off"
                    next_operator = None
                    asset.operator = None


        asset.save()
        asset_history_record = dict()
        asset_history_record['datetime'] = str(datetime.datetime.now())
        asset_history_record['action'] = 'ASSIGN'
        if asset.operator:
            asset_history_record['operator'] = asset.operator.pk
        else:
            asset_history_record['operator'] = 0

        asset_history_record['stationinroute'] = self.pk

        if 'history' not in asset.meta:
            asset.meta['history'] = list()
        asset.meta['history'].append(asset_history_record)

        asset.save()
        
        if asset.operator and self.properties.get('notify_operator',False):
    
            from nexus.notifications import nexus_sendmail
            msg_text = '<p>Здравствуйте!<p> Получено новое задание на технологическом этапе "'+self.station_name+'". Для перехода к заданию воспользуйтесь ссылкой: <a href="https://hub.sfedu.ru/dashboard/asset/'+str(asset.pk)+'/">https://hub.sfedu.ru/dashboard/asset/'+str(asset.pk)+'/</a>.';
            dictionary = {
                'to': asset.operator.username+'@sfedu.ru',
                'subject': 'hub.sfedu.ru - '+self.station_name+' - новое задание: '+asset.get_signature_string(),
                'body': msg_text,#'<p>Здравствуйте!<p> Получено новое задание на технологическом этапе "'+self.station_name+'" #'+asset.payload.get('uuid',[''])[0]+'. Вы являетесь оператором технологического этапа, и задание было поручено Вам в автоматическом режиме.<p>Для выполнения задания перейдите по следующей ссылке: <a href="http://hub.sfedu.ru/workstation/?station_id='+str(self.pk)+'&asset_id='+str(asset.pk)+'">http://hub.sfedu.ru/workstation/?station_id='+str(self.pk)+'&asset_id='+str(asset.pk)+'</a>',
                'DSN':True
            }
            if asset.meta.get('debug',False):
                dictionary['to'] = 'abogomolov@sfedu.ru'
            nexus_sendmail(dictionary)

        if debug:
            print('--- assign_asset calls station.perform')
        asset.stationinroute.station.perform(asset)
        if not suspend_further_routing:
            if debug:
                print('--- assign_asset calls asset_action')
            asset.route.asset_action(asset,suspend_further_routing=suspend_further_routing)
        else:
            if debug:
                print('suspend_further_routing=True, asset_action will not be called.')
        if debug:
            print('<<< assign_asset end')
            
    def __str__(self):
        return "" + self.station_name

    def add_asset(self,operator,stationinroute,payload):
        #use update_payload for payload manipulation
        result = dict()
        result['status'] = 200
        result['context'] = None
        if operator not in self.operators.all() and not self.properties.get('non_operator_adding_assets',False):
            result['status'] = 403
            result['message'] = str(operator)+' is not an operator of the station'
            return result

        route = stationinroute.route
        asset_type = stationinroute.route.default_asset_type

        asset = Asset()


        if operator in self.operators.all():
            asset.operator = operator
        asset.type = asset_type
        asset.route = route
        asset.stationinroute = stationinroute
        asset.meta['creator'] = operator.pk
        result_success,errors_list = self._check_supplied_data(asset.type.sysname,payload)
        if not result_success:
            result['status'] = 406
            result['message'] = 'Проверьте правильность введенных данных'
            result['errors_list'] = errors_list
        if result['status'] != 200:
            return result
        return self.update_payload(operator,asset,payload)


    def take_asset(self,operator,asset):

        result = dict()
        result['status'] = 200
        result['context'] = None	
        if operator not in self.operators.all():
            result['status'] = 403
            result['message'] = str(operator)+' is not an operator of the station'
            return result
        if self.properties.get('auto_assign_mode_on',False):
            result['status'] = 403
            result['message'] = str(operator)+' station\'s asset auto assign mode is on'
            return result
        asset.operator = operator
        asset.save()
        asset_history_record = dict()
        asset_history_record['datetime'] = str(datetime.datetime.now())
        asset_history_record['action'] = 'TAKE'
        asset_history_record['operator'] = operator.pk
        asset_history_record['stationinroute'] = self.pk

        if 'history' not in asset.meta:
            asset.meta['history'] = list()
        asset.meta['history'].append(asset_history_record)
        asset.save()
        asset.stationinroute.station.perform(asset)
        asset.route.asset_action(asset)

        context = {
            'asset_id':asset.pk,
        }
        result['context'] = context
        return result


    def _check_supplied_data(self,asset_type_sysname,payload,_field_templates=None):
        debug = False
        if payload.get('title',['']) == ['debug']:
            debug = True
        if asset_type_sysname == 'user_request':
            debug = True
        if debug:
            print("_check_supplied_data(",asset_type_sysname,",",payload,")")
        
        errors_list = list()
        try:
            asset_type=AssetType.objects.get(sysname=asset_type_sysname)
            got_errors = False
            for key in payload:
                if key.startswith('-'):
                    continue
                if len(payload[key]) > 1 and not asset_type.properties['fields'].get(key,{}).get('field_multiple',False):
                    errors_list.append({"field":key,"hint":"Поле не может иметь более одного значения"})
                    #print("disallowed multiple field - ",key)
                    got_errors = True
            if got_errors:
                if debug:
                    print("Exiting with errors:",errors_list)
                return False, errors_list
        except:
            errors_list.append({"field":"","hint":"Ошибка подсчета количества значений поля"})
            #print("error during multiple fields check")
            if debug:
                print("Exiting on exception with errors:",errors_list)

            return False, errors_list

        if not _field_templates:
            field_templates = self.get_field_templates(asset_type_sysname,payload)
        else:
            field_templates = _field_templates
        if debug:
            print("field_templates for ",asset_type_sysname,":",field_templates)
        got_errors = False
        for requirement in field_templates['required_fields']:
            requirement_fulfilled = True
            if '.' in requirement:
                requirement_is_complex = True
            else:
                requirement_is_complex = False
            if not requirement_is_complex:
                if requirement not in payload:
                    requirement_fulfilled = False
                else:
                    values_list = payload[requirement]
                    for proto_value in values_list:
                        value_type = type(proto_value).__name__
                        value = proto_value
                        if value_type != "dict":
                            try:
                                value = ast.literal_eval(proto_value)
                                value_type = type(value).__name__
                            except:
                                pass

                        if value_type != "dict":
                            if value == "":
                                requirement_fulfilled = False
                        else:
                            if "".join(value) == "":
                                requirement_fulfilled = False
            else: #requirement_is_complex
                temp = requirement.split('.')
                head = temp[0]
                tail = temp[1]

                if head not in payload:
                    pass
                else:
                    values_list = payload[head]
                    for proto_value in values_list:
                        value_type = type(proto_value).__name__
                        value = proto_value
                        if value_type != "dict":
                            try:
                                value = ast.literal_eval(proto_value)
                                value_type = type(value).__name__
                            except:
                                pass

                        if value_type != "dict":
                            requirement_fulfilled = False
                            #logger.debug('value is not a dict - failed')
                        else:
                            if value[tail] == "":
                                #logger.debug('value is empty - failed')
                                requirement_fulfilled = False
                            else:
                                #logger.debug('value is ok')
                                pass
            if not requirement_fulfilled:
                errors_list.append({"field":requirement,"hint":"Обязательное поле не заполнено"})
                got_errors = True
        if got_errors:
            return False,errors_list
        return True,errors_list

    def _restore_filefield_dicts(self,operator,asset,payload):
        #replacing every fielfield guid with corresponding filefield dict form asset.payload
        #non-compound fields processing
        for key in payload:
            #filefields that are marked for deletion needn't to be replaced with file dict
            fieldkey = key
            if key[0] == '-': 
                #continue
                fieldkey = key[1:]

            #if field somehow doesn't have a masterfield, it can't be worked with
            try:
                masterfield = MasterField.objects.filter(sysname=fieldkey).first()
            except:
                continue


            #compound fields are skipped for later
            if masterfield.properties.get('field_compound', False):
                #logger.debug('compound - passing it on')
                continue



            #if field is not filefield, skip it
            if not masterfield.properties.get('is_filefield',False):
                #logger.debug('not filefield, skipping')
                continue

            #if field somehow isn't represented in asset.payload and is not nested filefield, e.g. -parent.nestedfile, skip it
            if fieldkey not in asset.payload:
                #logger.debug('not in asset.payload')
                if not masterfield.properties.get('field_nested', False):
                    #logger.debug('... and not nested, skipping')
                    continue

            for idx,uuid in enumerate(payload[key]):
                #logger.debug('processing '+uuid+' / '+str(idx))
                #news filefields - i.e. arrived as dicts, are of no interest, skip
                if isinstance(uuid,dict):
                    #logger.debug('dict, passing on')
                    continue

                #nested filefields require special treatment
                if not masterfield.properties.get('field_nested', False):
                    #looking for filefiled uuid in asset.payload
                    #logger.debug('not nested field')
                    for item in asset.payload[fieldkey]:
                        if item['uuid'] == uuid:
                            #logger.debug('... found in payload, breaking')
                            payload[key][idx] = item
                            break
                else:
                    #logger.debug('nested field')
                    fieldname = fieldkey.split('.')
                    #logger.debug('nested field special treatment')
                    #logger.debug(fieldname)
                    #logger.debug(uuid)

                    for item in asset.payload[fieldname[0]]:
                        #logger.debug(item)
                        if item[fieldname[1]]['uuid'] == uuid:
                            #logger.debug('found uuid, modified payload')
                            payload[key][idx] = item[fieldname[1]]
                            break


                        
        #compound fields processing
        for key in payload:
            #filefields that are marked for deletion needn't to be replaced with file dict
            fieldkey = key
            if key[0] == '-': 
                fieldkey = key[1:]

            #if field somehow doesn't have a masterfield, it can't be worked with
            try:
                masterfield = MasterField.objects.filter(sysname=fieldkey).first()
            except:
                continue
            #non-compound fields are processed before
            if not masterfield.properties.get('field_compound', False):
                #logger.debug('not compound, passing on')
                continue


            #if field somehow isn't represented in asset.payload, skip it
            if fieldkey not in asset.payload:
                continue

            logger.debug('beginning to process values')
            for idx,compound_value in enumerate(payload[key]):
                #news filefields - i.e. arrived as dicts, are of no interest, skip

                if not isinstance(compound_value, dict):
                    #logger.debug('value is not dict, passing on')
                    continue

                #checking nested fields for being filefields
                for nested_field in compound_value:
                    nested_masterfield = MasterField.objects.filter(sysname=fieldkey+'.'+nested_field).first()


                    #if nested field is not filefield, skip it
                    if not nested_masterfield.properties.get('is_filefield',False):
                        continue

                    #looking for uuid in asset.payload
                    if isinstance(compound_value[nested_field], dict):
                        #logger.debug('nested field is dict, passing on')
                        continue

                    for item in asset.payload[fieldkey]:
                        if isinstance(item[nested_field], dict) and item[nested_field]['uuid'] == compound_value[nested_field]:
                            payload[key][idx][nested_field] = item[nested_field]
                            break

    def update_payload(self,operator,asset,payload):
        debug = False
        #debug = True
        if payload.get('title',['']) == ['debug']:
            debug = True

        if debug:
            print("update_payload(",operator,",",asset,",",payload,")")
        result = dict()
        result['status'] = 200
        result['url'] = None
        result['context'] = None

        dumpster = dict()
        if operator not in self.operators.all() and not self.properties.get('non_operator_adding_assets',False) and not self.properties.get('creator_operator',False):
            result['status'] = 403
            result['message'] = str(operator)+' is not an operator of the station'
            if debug:
                print(result)
            return result
        if not asset.stationinroute.station == self:
            result['status'] = 403
            result['message'] = 'Station #' + str(self.pk) + ' is not asset\'s host'
            if debug:
                print(result)
            return result

        if debug:
            print("about to invoke _check_supplied_data")

        field_templates = self.get_field_templates(asset.type.sysname,asset.payload)
        result_success,errors_list=self._check_supplied_data(asset.type.sysname,payload,field_templates)
        if debug:
            print("_check_supplied_data result:",result_success,", errors:",errors_list)
        if not result_success:
            #print "update_payload is returning 406"
            result['status'] = 406
            result['message'] = 'Проверьте правильность заполнения полей'
            result['errors_list'] = errors_list
            if debug:
                print("_check_suppied data error:",result)
            return result

        #pop every key that arrived in payload parameter
        #pop every key with  "-" as first char, pay attention to files and files in compound fields
        #fill the asset's payload with the remains of the payload parameter
        #save files to disk = "chunks" property of file fields

        for key in payload:
            if key[0] != '-':
                field_name = key
            else:
                field_name=key[1:]

            masterfield = MasterField.objects.filter(sysname=field_name).first()


            if not masterfield:
                result['status'] = 500
                result['message'] = 'masterfield not found: '+field_name
                if debug:
                    print(result)
                return result

            if key[0] != '-':
                dump = asset.payload.pop(key,None)

            else:
                pass

                if key[1:] not in dumpster:
                    dumpster[key[1:]] = list()
                for item in payload[key]:
                    dumpster[key[1:]].append(item)

                dump = asset.payload.pop(key[1:],None)

        asset.save()
        for key in payload:
            #logger.debug(key)
            final_value = payload[key]

            if key[0] != '-':
                masterfield = MasterField.objects.get(sysname=key)

                for idx, final_subvalue in enumerate(final_value):
                    if masterfield.properties.get('field_compound',False):
                        #compound field, must check for fileness its nested fields
                        for nested_field in final_subvalue:
                            if debug:
                                print("getting",masterfield.sysname,'.',nested_field)
                            nested_masterfield = MasterField.objects.get(sysname=masterfield.sysname+'.'+nested_field)
                            if debug:
                                print(" // done")
                            #logger.debug('processing subfield '+nested_masterfield.sysname)
                            if isinstance(final_subvalue[nested_field], dict) and nested_masterfield.properties.get('is_filefield',False) and 'chunks' in final_subvalue[nested_field]:
                                #logger.debug('subfield is filefield //'+str(nested_field))
                                file_dict = final_subvalue[nested_field]
                                if nested_masterfield.properties.get('default_file_storage',False) and nested_masterfield.properties['default_file_storage'] in FileStorage.objects.all().values_list('id', flat=True):
                                    file_dict['storage'] = nested_masterfield.properties['default_file_storage']
                                if masterfield.properties.get('default_file_storage',False) and masterfield.properties['default_file_storage'] in FileStorage.objects.all().values_list('id', flat=True):
                                    file_dict['storage'] = masterfield.properties['default_file_storage']
                                elif asset.type.properties.get('default_file_storage',False) and asset.type.properties['default_file_storage'] in FileStorage.objects.all().values_list('id', flat=True):
                                    file_dict['storage'] = asset.type.properties['default_file_storage']
                                elif asset.stationinroute.station.properties.get('default_file_storage',False) and asset.stationinroute.station.properties['default_file_storage'] in FileStorage.objects.all().values_list('id', flat=True):
                                    file_dict['storage'] = asset.stationinroute.station.properties['default_file_storage']
                                else:
                                    file_dict['storage'] = FileStorage.objects.first().pk

                                file_path = FileStorage.objects.get(pk=int(file_dict['storage'])).path+str(asset.pk)+'/'+file_dict['uuid']+'/'
                                #logger.info('file path:'+file_path)
                                if not os.path.exists(file_path):
                                    os.makedirs(file_path)

                                with open(file_path+file_dict['filename'], 'wb+') as destination:
                                    for chunk in file_dict['chunks']:
                                        destination.write(chunk)

                            else:
                                if nested_masterfield.properties.get('is_filefield',False):
                                    #filefield that is passed as string object(uuid) - let's replace it with full dict from the asset.payload

                                    for value in asset.payload.get(key,list()):
                                        if value[nested_masterfield.sysname]['uuid'] == nested_field:
                                            payload[key][idx][nested_masterfield.sysname] = value

                    else:
                        if isinstance(final_subvalue, dict):
                            subvalue_keys = ''
                            for dumpkey in final_subvalue:
                                subvalue_keys+=dumpkey+','
                            if 'chunks' in final_subvalue and 'filename' in final_subvalue and 'uuid' in final_subvalue:
                                file_dict = final_subvalue
                                #getting storage: first ask master_field, then asset_type, then station, then take first in a list
                                if masterfield.properties.get('default_file_storage',False) and masterfield.properties['default_file_storage'] in FileStorage.objects.all().values_list('id', flat=True):
                                    file_dict['storage'] = masterfield.properties['default_file_storage']
                                elif asset.type.properties.get('default_file_storage',False) and asset.type.properties['default_file_storage'] in FileStorage.objects.all().values_list('id', flat=True):
                                    file_dict['storage'] = asset.type.properties['default_file_storage']
                                elif asset.stationinroute.station.properties.get('default_file_storage',False) and asset.stationinroute.station.properties['default_file_storage'] in FileStorage.objects.all().values_list('id', flat=True):
                                    file_dict['storage'] = asset.stationinroute.station.properties['default_file_storage']
                                else:
                                    file_dict['storage'] = FileStorage.objects.first().pk

                                file_path = FileStorage.objects.get(pk=int(file_dict['storage'])).path+str(asset.pk)+'/'+file_dict['uuid']+'/'
                                if not os.path.exists(file_path):
                                    os.makedirs(file_path)


                                try:
                                    with open(file_path+file_dict['filename'], 'wb+') as destination:
                                        for chunk in final_subvalue['chunks']:
                                            destination.write(chunk)
                                except:
                                    original_file_title,original_file_ext = os.path.splitext(file_dict['filename'])
                                    file_dict['filename'] = file_dict['uuid']+original_file_ext

                                    with open(file_path+file_dict['uuid']+original_file_ext, 'wb+') as destination:
                                        for chunk in final_subvalue['chunks']:
                                            destination.write(chunk)
                                    #renamed_files_uuids.append(file_dict['uuid'])

                        else:
                            if masterfield.properties.get('is_filefield',False):
                                #filefield that is passed as string object(uuid) - let's replace it with full dict from the asset.payload
                                for value in asset.payload.get(key,list()):
                                    if value['uuid'] == final_subvalue:
                                        payload[key][idx] = value

                asset.payload[key] = final_value


                #erasing chunks - they can't be serialized
                for subvalue in payload[key]:
                    if isinstance(subvalue, dict) and 'chunks' in subvalue:
                        dump = subvalue.pop('chunks',None)
                    if masterfield.properties.get('field_compound', False):
                        for nested_subvalue in subvalue:
                            if isinstance(subvalue[nested_subvalue],dict) and 'chunks' in subvalue[nested_subvalue]:
                                dump = subvalue[nested_subvalue].pop('chunks', None)

                for subvalue in asset.payload[key]:
                    if isinstance(subvalue, dict) and 'chunks' in subvalue:
                        dump = subvalue.pop('chunks',None)
                    if masterfield.properties.get('field_compound', False):
                        for nested_subvalue in subvalue:
                            if isinstance(subvalue[nested_subvalue],dict) and 'chunks' in subvalue[nested_subvalue]:
                                dump = subvalue[nested_subvalue].pop('chunks', None)


        for item in dumpster:
            for value in dumpster[item]:
                if isinstance(value,dict):
                    #a filefield or a compound
                    if 'filename' in value and 'uuid' in value:
                        #a filefield
                        file_dict = value
                        file_path = FileStorage.objects.get(pk=int(file_dict['storage'])).path+str(asset.pk)+'/'+file_dict['uuid']
                        try:
                            shutil.rmtree(file_path)
                        except:
                            pass
                    else:
                        #a compound
                        for subkey in value:
                            if isinstance(value[subkey],dict):
                                #logger.debug(subkey+' is a dict')
                                if 'filename' in value[subkey] and 'uuid' in value[subkey]:
                                    #logger.debug('filefield')
                                    file_dict = value[subkey]
                                    file_path = FileStorage.objects.get(pk=int(file_dict['storage'])).path+str(asset.pk)+'/'+file_dict['uuid']
                                    #logger.debug(file_path)
                                    try:
                                        shutil.rmtree(file_path)
                                        #logger.debug('deleted')
                                    except:
                                        #logger.debug('error deleting file')
                                        pass


        if result['status'] == 200:
            asset_history_record = dict()
            asset_history_record['datetime'] = str(datetime.datetime.now())
            asset_history_record['action'] = 'UPDATE'
            asset_history_record['operator'] = operator.pk
            asset_history_record['stationinroute'] = asset.stationinroute.pk
            if 'logging' in self.properties:
                if 'level' in self.properties['logging']:
                    if self.properties['logging']['level'] == 'full':
                        asset_history_record['data'] = payload

            if 'history' not in asset.meta:
                asset.meta['history'] = list()
            asset.meta['history'].append(asset_history_record)



            asset.save()

            asset.stationinroute.station.perform(asset)
            asset.route.asset_action(asset)

            result['stationinroute_id'] = asset.stationinroute.pk
            result['station_id'] = asset.stationinroute.station.pk
            result['station_name'] = asset.stationinroute.station.station_name
            result['asset_id'] = asset.pk
        else:
            asset = Asset.objects.get(pk=int(asset_id))
            if debug:
                try:
                    print(result)
                except:
                    pass



        if debug:
            print("SUCCESSFULLY RETURNING, result=",result)
        return result

class PublicationLogEntry(models.Model):
    entry_datetime = models.DateTimeField(default=datetime.datetime.now,)
    asset = models.ForeignKey('Asset',related_name='+',on_delete=models.CASCADE)
    user = models.ForeignKey(User,blank=True,null=True,related_name='+',on_delete=models.SET_NULL)
    username = models.CharField(max_length=100,blank=True,null=True,)
    access_download_file = models.BooleanField(default=False,)
    access_read_file = models.BooleanField(default=False,)
    access_description = models.BooleanField(default=False,)

class Group_caption(models.Model):
    group = models.OneToOneField(Group,on_delete = models.CASCADE,related_name='caption',)
    caption = models.CharField(max_length=255,blank=False,null=False,)