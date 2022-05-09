import ipaddress
from django.db.models import Q
from django.contrib.auth.models import User,AnonymousUser
from dateutil.relativedelta import relativedelta
from datetime import datetime

def get_user_info(user,asset,stationinroute,station):
    is_creator = False
    is_operator = False
    is_supervisor = False
    is_authenticated_user = False
    ip_address = None

    if user: 
        is_authenticated_user = user.is_authenticated

        if hasattr(user,'ip_address'):
            ip_address = user.ip_address

        if asset:
            if 'creator' in asset.meta:
                try:
                    operator = User.objects.get(pk=asset.meta['creator'])
                    if user == operator:
                        is_creator = True
                except:
                    pass
            if 'creator_str' in asset.meta:
                try:
                    operator = User.objects.get(pk=int(asset.meta['creator_str']))
                    if user == operator:
                        is_creator = True
                except:
                    pass
            if 'publication_creators' in asset.meta:
                for cr in asset.meta['publication_creators']:
                    if cr['pk'] == user.pk:
                        is_creator = True

            if asset.stationinroute:
                if user in asset.stationinroute.station.operators.all():
                    is_operator = True
                if user in asset.stationinroute.station.supervisors.all():
                    is_supervisor = True


        if stationinroute:
            if user in stationinroute.station.operators.all():
                is_operator = True
            if user in stationinroute.station.supervisors.all():
                is_supervisor = True

        if station:
            if user in station.operators.all():
                is_operator = True
            if user in station.supervisors.all():
                is_supervisor = True
    return {'is_operator':is_operator,'is_supervisor':is_supervisor,'is_creator':is_creator,'is_authenticated_user':is_authenticated_user,'ip_address':ip_address}

def get_permissions_list(action=None,asset=None,_stationinroute=None,_station=None,_route=None,_asset_type=None,user=None,user_groups=list(),authenticated_user=False,debug=False):

    if asset and asset.pk==1269217:#819786:
        debug=True

    is_creator = False
    is_operator = False
    is_supervisor = False
    is_authenticated_user = False
    ip_address = None
    user_groups=list()
    #collect user info
    if user and len(user_groups) == 0:
        try:
            user_groups = user.groups.all()
        except:
            user_groups = list()
    debug=True
    if debug:
        print("user:",user,"groups:",user_groups)
    debug=False
    user_info = get_user_info(user,asset,_stationinroute,_station)
    if debug:
        print('\n\n===\n\n',action,user_info,'\n\n===\n\n')
        #debug=False
    if isinstance(user,AnonymousUser):
        user=None
    is_operator = user_info['is_operator']
    is_supervisor = user_info['is_supervisor']
    is_creator = user_info['is_creator']
    is_authenticated_user = user_info['is_authenticated_user']
    ip_address = user_info['ip_address']

    if authenticated_user:
        is_authenticated_user = True


    from nexus.models import Nexus_permission
    
    stationinroute = _stationinroute
    station = _station
    route = _route
    asset_type = _asset_type

    if asset:
        
        if not station:
            station = asset.stationinroute.station
        if not stationinroute:
            stationinroute=asset.stationinroute
        if not route:
            route = asset.stationinroute.route
        
        if not asset_type:
            asset_type = asset.type
    
    if stationinroute:
        if not station:
            station = asset.stationinroute.station
        if not route:
            route = asset.stationinroute.route
    

    perms = Nexus_permission.objects.all()
    
    if debug:
        print("perms total count: ", perms.count())
    
    if not is_supervisor:
        perms = perms.filter(Q(is_supervisor__isnull=True)|Q(is_supervisor=False))
    if not is_operator:
        perms = perms.filter(Q(is_operator__isnull=True)|Q(is_operator=False))
    if not is_creator:
        perms = perms.filter(Q(is_creator__isnull=True)|Q(is_creator=False))
    if not is_authenticated_user:
        perms = perms.filter(Q(is_authenticated_user__isnull=True)|Q(is_authenticated_user=False))
    
    if debug:
        print("is_supervisor: ",is_supervisor)
        print("is_operator: ",is_operator)
        print("is_creator: ",is_creator)
        print("is_authenticated_user: ",is_authenticated_user)
        print("available actions:")
        for perm in perms:
            print(perm.pk,' ',perm.permission_action_sysname)
    
    if action and action.strip() != '':
        perms = perms.filter(permission_action_sysname=action)
    else:
        perms = perms.filter(Q(permission_action_sysname__isnull=True)|Q(permission_action_sysname=''))
    
    if debug:
        print("action: ",action)
        print("perms filtered by action: ", perms.count())
        print("available assets:")
        for perm in perms:
            print(perm.pk,' ',perm.asset)
    
    perms = perms.filter(Q(asset__isnull=True)|Q(asset=asset))
    
    if debug:
        print("asset: ",asset)
        print("perms filtered by asset: ", perms.count())
        print("available stationinroutes:")
        for perm in perms:
            print (perm.pk,' ',perm.stationinroute)
    
    
    perms = perms.filter(Q(stationinroute__isnull=True)|Q(stationinroute=stationinroute))
    
    
    
    if debug:
        print("stationinroute: ",stationinroute)
        print("perms filtered by stationinroute: ", perms.count())
        print("available stations:")
        for perm in perms:
            print(perm.pk,' ',perm.station)
    
    
    
    perms = perms.filter(Q(station__isnull=True)|Q(station=station))
    
    
    
    if debug:
        print("station: ", station)
        print("perms filtered by station: ",perms.count())
        print("available routes:")
        for perm in perms:
            print(perm.pk,' ',perm.route)
    
    
    
    perms = perms.filter(Q(route__isnull=True)|Q(route=route))
    
    
    if debug:
        print("route: ",route)
        print("perms filtered by route: ", perms.count())
        print("available asset_types:")
        for perm in perms:
            print(perm.pk,' ',perm.asset_type)
    
    
    
    perms = perms.filter(Q(asset_type__isnull=True)|Q(asset_type=asset_type))
    
    if debug:
        print("asset_type: ",asset_type)
        print("perms filtered by asset_type: ",perms.count())
        print("available users:")
        for perm in perms:
            print(perm.pk,' ',perm.user)
    if user:
        perms = perms.filter(Q(user__isnull=True)|Q(user=user))
    else:
        perms = perms.filter(user__isnull=True)
        

    if debug:
        print("user: ",user)
        print("perms filtered by user: ", perms.count())
        print("available groups:")
        print("user_groups:",user_groups)
        for perm in perms:
            print(perm.pk,' ',perm.group)
    
    perms = perms.filter(Q(group__isnull=True)|Q(group__in=user_groups))
    
    
    if debug:
        print("perms filtered by groups: ", perms.count())
    
    #check payload values

    if asset:
        permissions_to_exclude = list()
        for perm in perms:
            if perm.payload_value:
                
                #print "perm record #%s has payload_value filter" % str(perm.pk)
                for key in perm.payload_value:
                    #print "evaluating %s" % key
                    if debug:
                        print('perm has payload value requirement:',key,perm.payload_value[key])
                    if '.' in key:
                        #compound requirement
                        masterkey,subkey=key.split('.')
                        if (not masterkey in asset.payload) or (len(asset.payload[masterkey]) == 0) or (not subkey in asset.payload[masterkey][0]):
                            if debug:
                                print("no key or key is empty in asset, excluding the perm #",perm.pk)
                            permissions_to_exclude.append(perm.pk)
                            continue
                        
                        if not isinstance(perm.payload_value[key], dict):
                            #print "key is present, not empty and is not dict, validated"
                            pass
                        else:
                            cmp_op = perm.payload_value[key]['cmp_op']
                            cmp_val = perm.payload_value[key].get('cmp_val',None)
                            if cmp_op == "=":
                                if debug:
                                    print("cmp_op is '=', cmp_value is",cmp_val)
                                if isinstance(asset.payload[masterkey][0][subkey], str):
                                    if cmp_val.strip().lower() != asset.payload[masterkey][0][subkey].strip().lower():
                                        permissions_to_exclude.append(perm.pk)
                                else:
                                    if debug:
                                        print("asset.payload[",masterkey,"][0][",subkey,"] is not str")

                                    if cmp_val != asset.payload[masterkey][0][subkey]:
                                        if debug:
                                            print("excluding permission: cmp_val is not equal to asset.payload[",key,"][0]:",cmp_val,"<>",asset.payload[key][0])
                                        permissions_to_exclude.append(perm.pk)
                        continue
                    if (not key in asset.payload) or (len(asset.payload[key]) == 0):
                        if debug:
                            print("no key or key is empty in asset, excluding the perm #",perm.pk)
                        permissions_to_exclude.append(perm.pk)

                    else:
                        #it is ok (present in asset.payload) if it's not dict - must check value then
                        if isinstance(perm.payload_value[key], dict):
                            if debug:
                                print("key is in perm and is dict")
                            #check value correspondence
                            cmp_op = perm.payload_value[key]['cmp_op']
                            cmp_val = perm.payload_value[key].get('cmp_val',None)
                            
                            #datetime special care: cmp_val is a string starting with DATETIME_, datetime_formats is in perm keys alongside cmp_op and cmp_val
                            if isinstance(asset.payload[key][0], str) and isinstance(cmp_val,str) and cmp_val.startswith('DATETIME_') and 'datetime_formats' in perm.payload_value[key]:
                                debug=True
                                if debug:
                                    print('datetime comparison: cmp_op is',cmp_op,',cmp_val is',cmp_val,',datetime formats are',perm.payload_value[key]['datetime_formats'])
                                found_format = False
                                for fmt in perm.payload_value[key]['datetime_formats']:
                                    try:
                                        if debug:
                                            print('trying format',fmt,'to value',asset.payload[key][0])
                                        dt_object=datetime.strptime(asset.payload[key][0],fmt)
                                        found_format = True
                                        if debug:
                                            print('format seems to fit:',dt_object)
                                        cmp_val_list = cmp_val.split('_')
                                        if cmp_val_list[1] == 'PLUSMONTHS':
                                            if debug:
                                                print('trying ',cmp_val_list[1])
                                            dt_val = int(cmp_val_list[2])
                                            delta = relativedelta(months=dt_val)
                                            if debug:
                                                print('got delta', delta)
                                            if cmp_op =='<' and (dt_object+delta<datetime.now()):
                                                permissions_to_exclude.append(perm.pk)
                                                if debug:
                                                    print(cmp_op,'test fail.')
                                                break
                                            if cmp_op =='>' and (dt_object+delta>datetime.now()):
                                                permissions_to_exclude.append(perm.pk)
                                                if debug:
                                                    print(cmp_op,'test fail..')
                                                break
                                            if debug:
                                                print('CHECK SUCCESS')
                                    except:
                                        if debug:
                                            print('format doesn\'t fit the value')
                                        continue
                                if not found_format:
                                    permissions_to_exclude.append(perm.pk)

                                continue

                            if cmp_op == "=":
                                if debug:
                                    print("cmp_op is '=', cmp_value is",cmp_val)
                                if isinstance(asset.payload[key][0], str):
                                    if cmp_val.strip().lower() != asset.payload[key][0].strip().lower():
                                        permissions_to_exclude.append(perm.pk)
                                else:
                                    if debug:
                                        print("asset.payload[",key,"][0] is not str")

                                    if cmp_val != asset.payload[key][0]:
                                        if debug:
                                            print("escluding permission: cmp_val is not equal to asset.payload[",key,"][0]:",cmp_val,"<>",asset.payload[key][0])
                                        permissions_to_exclude.append(perm.pk)
                            elif cmp_op == ">":
                                if isinstance(asset.payload[key][0], str):
                                    #can't compare strings in that way, False
                                    permissions_to_exclude.append(perm.pk)
                                else:
                                    try:
                                        if cmp_val >= asset.payload[key][0]:
                                            permissions_to_exclude.append(perm.pk)
                                    except:
                                        permissions_to_exclude.append(perm.pk)
                            elif cmp_op.lower() == "exists":
                                #not excluding anything - cmp_op="exists" and key in asset.payload
                                pass
                            else:# cmp_op == "<":
                                if isinstance(asset.payload[key][0], str):
                                    #can't campare strings in that way, False
                                    permissions_to_exclude.append(perm.pk)
                                else:
                                    try:
                                        if cmp_val <= asset.payload[key][0]:
                                            permissions_to_exclude.append(perm.pk)
                                    except:
                                        permissions_to_exclude.append(perm.pk)
                        else:
                            #print "key is present, not empty and is not dict, validated"
                            pass
        if debug:
            print("excluding ",len(permissions_to_exclude),"out of ",perms.count()," perms")
        perms=perms.exclude(id__in=permissions_to_exclude)
        
        permissions_to_exclude = list()
        for perm in perms:
            if perm.ip_range:
                if not ip_address:
                    permissions_to_exclude.append(perm.pk)
                    if debug:
                        print("client ip address is not supplied, excluding permission:",ip_address)
                else:
                    if not ipaddress.ip_address(ip_address) in ipaddress.ip_network(perm.ip_range):
                        if debug:
                            print(ip_address,"does not belong to ip range",perm.ip_range)
                        permissions_to_exclude.append(perm.pk)

        if debug:
            print("excluding ",len(permissions_to_exclude),"out of ",perms.count()," perms based on ip_range property")
        perms=perms.exclude(id__in=permissions_to_exclude)
        

        if debug:
            print("resulting perms length is ",perms.count())
        if debug:
            for perm in perms:
                print(perm)
            
        
    return perms


def perform_check(user, asset_type, route, station, stationinroute, asset, action, debug):
    #print("perform_check start")
    result = False
    result_permission = None
    is_creator = False
    is_operator = False
    is_supervisor = False
    is_authenticated_user = False
    user_groups = None

    #collect user info
    user_info = get_user_info(user,asset,stationinroute,station)
    is_operator = user_info['is_operator']
    is_supervisor = user_info['is_supervisor']
    is_creator = user_info['is_creator']
    is_authenticated_user = user_info['is_authenticated_user']
    if user:
        try:
            user_groups = list(user.groups.all())
        except:
            user_groups = list()
    else:
        user_groups = list()

    _stationinroute = stationinroute
    _station=station
    _route=route
    _asset_type=asset_type

    if asset:
        if not asset_type:
            _asset_type=asset.type
        
        if not stationinroute:
            _stationinroute=asset.stationinroute
        if not route:
            _route=asset.stationinroute.route
        if not station:
            _station = asset.stationinroute.station
        
    perms = get_permissions_list(action=action,asset=asset,_stationinroute=_stationinroute,_station=_station,_route=_route,_asset_type=_asset_type,user=user,user_groups=user_groups, debug=debug)
    prohibitions = perms.filter(is_prohibition=True)
    perms = perms.filter(is_prohibition=False)

    #check defaults
    default_prohibitions = prohibitions.filter(is_default=True)
    default_perms = perms.filter(is_default=True)
    if (not result_permission) and (default_prohibitions.count() > 0):
        result_permission=default_prohibitions.first()
        result = False
    if (not result_permission) and (default_perms.count() > 0):
        result_permission=default_perms.first()
        result = True

    #check permissions and prohibitions
    perms = perms.filter(is_default=False)
    prohibitions = prohibitions.filter(is_default=False)
    if (not result_permission) and (prohibitions.count() > 0):
        #print("got false by prohibition")
        result_permission=prohibitions.first()
        result = False
        #print(result_permission)
    if (not result_permission) and (perms.count() > 0):
        #print("got true")
        result_permission=perms.first()
        #print(result_permission)
        result = True

    #log

    if result_permission:
        if (result_permission.logging == 3) or (result_permission.logging == 2 and result) or (result_permission.logging == 12 and not result):
            from nexus.models import Nexus_permission_log_entry

            entry = Nexus_permission_log_entry()
            entry.permission=result_permission
            entry.permission_action_sysname = action
            entry.entry_result = result
            if not isinstance(user,AnonymousUser):
                entry.user = user
            entry.asset_type = asset_type
            entry.asset = asset
            entry.station = station
            entry.route = route
            entry.stationinroute = stationinroute
            entry.save()
    

    return result

