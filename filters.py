import datetime
import operator
from datetime import timedelta

from functools import reduce
from django.db.models import Q
from django.db.models.expressions import RawSQL
from rest_framework import filters
from common.elasticsearch import *

class AssetTypeFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        type = request.query_params.get('type', None)
        if type:
            types = type.split(",")

            if len(types)>1:
                int_types=list()
                for item in types:
                    int_types.append(int(item))
                query = reduce(lambda q,value: q|Q(id=value), int_types, Q())
                queryset=queryset.filter(query)

            else:
                queryset=queryset.filter(id=int(type))
        return queryset

class PublicationLogEntryFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):

        date_filter = request.query_params.get('date_filter',None)

        if date_filter:
            if date_filter.startswith('today-'):
                rewind_days = date_filter.split('-')[1].strip()
                try:
                    date_str = (datetime.datetime.now()-timedelta(int(rewind_days))).strftime('%Y-%m-%d')+' 23:59:59'
                    queryset = queryset.filter(entry_datetime__lte=date_str)
                except:
                    pass
        return queryset

class StationFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):

        allow_adding_assets_filter = request.query_params.get('allow_adding_assets',None)

        if allow_adding_assets_filter in [True,'True','true']:
            try:
                queryset = queryset.filter(station_routes__properties__allow_adding_assets=True)
            except:
                pass

        return queryset
        
class UserFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):

        user_filter = request.query_params.get('query',None)

        if user_filter:
            try:
                queryset = queryset.filter(Q(last_name__icontains=user_filter)|Q(username__icontains=user_filter))
            except:
                pass

        return queryset

class AssetFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):

        queries = list()

        creator = request.query_params.get('creator', None)
        if creator:
            creators = creator.split(",")

            if len(creators)>1:
                int_creators=list()
                for item in creators:
                    int_creators.append(int(item))
                query = reduce(lambda q,value: q|Q(meta__creator=value), int_creators, Q())
                queryset=queryset.filter(query)

            else:
                queryset=queryset.filter(meta__creator=int(creator))
        fulltext_only = request.query_params.get('fulltext_only',False)
        if fulltext_only in ['True','true',True]:
            queryset=queryset.exclude(~Q(payload__has_key='fulltext')&Q(payload__has_key='fulltext_excerpt'))
        
        type_ = request.query_params.get('type', None)
        if type_:
            if type_ == 'all':
                from nexus.models import AssetType
                types = list(AssetType.objects.all().values_list('id',flat=True))
            else:
                types = type_.split(",")

            if len(types)>1:
                int_types=list()
                for item in types:
                    if item != 'all':
                        int_types.append(int(item))
                query = reduce(lambda q,value: q|Q(type__id=value), int_types, Q())
                queryset=queryset.filter(query)

            else:
                queryset=queryset.filter(type__id=int(type_))
        has_key_ = request.query_params.get('payload__has_key',None)
        if has_key_:
            has_keys = has_key_.split(",")

            for key in has_keys:
                queryset=queryset.filter(payload__has_key=key)


        station = request.query_params.get('station', None)
        if station:
            stations = station.split(",")

            if len(stations)>1:
                int_stations=list()
                for item in stations:
                    int_stations.append(int(item))
                query = reduce(lambda q,value: q|Q(stationinroute__station__id=value), int_stations, Q())
                queryset=queryset.filter(query)

            else:
                queryset=queryset.filter(stationinroute__station__id=int(station))

        if 'fulltext_query' in request.query_params:
            """
                fulltext search utilizes elasticsearch, not queryset filtering, therefore it is processed separately, 
                then creates queryset out of list of found ids and returns from filter_queryset
            """
            query = request.query_params['fulltext_query']
            if 'type' in request.query_params:
                if request.query_params['type'] == 'all':
                    from nexus.models import AssetType
                    types = list(AssetType.objects.all().values_list('id',flat=True))
                    #print(types)
                else:
                    types = request.query_params['type'].split(',')
                search_results = asset_search_all(query,type_list=types)
            else:
                search_results = asset_search_all(query)
            queryset = queryset.filter(pk__in=search_results['results'])
            sorted_queryset = list(queryset)
            sorted_queryset.sort(key=lambda t: search_results['results'].index(t.pk))
            return sorted_queryset

                
        #payload key filter
        for param_name in request.query_params:
            if not param_name.startswith('payload__'):
                continue
            if param_name == 'payload__has_key':
                continue
            if not param_name.endswith('__icontains'):
                if request.query_params[param_name].startswith('"') and request.query_params[param_name].endswith('"'):
                    param_value = [request.query_params[param_name][1:-1]]
                else:
                    param_value = request.query_params[param_name].split(",")

                queryset=queryset.filter(**{param_name:param_value})
            else:
                param_value = request.query_params[param_name]
                if '|' in param_name:
                    param_names = param_name.replace("__icontains","").replace("payload__","").split('|')
                    query = ''
                    for name in param_names:
                        query += '"nexus_asset"."payload" ->> \''+name+'\' ~* \''+param_value+'\' or '
                    query = query[:-4]
                else:
                    query = '"nexus_asset"."payload" ->> \''+param_name.replace("__icontains","").replace("payload__","")+'\' ~* \''+param_value+'\''
                queryset = queryset.extra(where=[query])
        

        return queryset

class PublicationsFilterBackend(AssetFilterBackend):

    def filter_queryset(self, request, queryset, view):
        if 'publication_creators' in request.query_params:
            if ',' in request.query_params['publication_creators']:
                #filter creators using AND
                op = operator.and_
                separator_char=','
            else:
                op = operator.or_
                separator_char='|'
            param_values = request.query_params['publication_creators'].split(separator_char)

            creators_ids = []
            creators_ids_str = []
            
            for param_value in param_values:
                try:
                    creator_id = User.objects.get(username=param_value).pk
                except:
                    creator_id=0
                creators_ids.append(creator_id)
                creators_ids_str.append(str(creator_id))

            
            query_list = []
            for param_value in param_values:
                query_list.append(Q(meta__publication_creators__contains=[{'username':param_value}]))
            query = reduce(op, query_list)
            
            queryset = queryset.filter(
                query|
                Q(
                    Q(
                        ~Q(meta__has_key='publication_creators_complete')|
                        ~Q(meta__publication_creators_complete=True)
                    ),
                    Q(meta__creator__in=creators_ids)|
                    Q(meta__creator_str__in=str(creators_ids_str))
                )
            )


        queryset = super().filter_queryset(request, queryset, view)
        return queryset

class RouteRecordFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        queries = list()

        operator = request.query_params.get('operator', None)
        if operator:
            operators = operator.split(",")

            if len(operators)>1:
                str_operators=list()
                for item in operators:
                    str_operators.append(item)
                query = reduce(lambda q,value: q|Q(operator__username=value), str_operators, Q())
                queryset=queryset.filter(query)

            else:
                queryset=queryset.filter(operator__username=operator)

        route = request.query_params.get('route', None)
        if route:
            routes = route.split(",")

            if len(routes)>1:
                int_routes=list()
                for item in routes:
                    int_routes.append(int(item))
                query = reduce(lambda q,value: q|Q(route__pk=value), int_routes, Q())
                queryset=queryset.filter(query)

            else:
                queryset=queryset.filter(route__pk=int(route))

        stationinroute = request.query_params.get('stationinroute', None)
        if stationinroute:
            stationinroutes = stationinroute.split(",")

            if len(stationinroutes)>1:
                int_stationinroutes=list()
                for item in stationinroutes:
                    int_stationinroutes.append(int(item))
                query = reduce(lambda q,value: q|Q(stationinroute__pk=value), int_stationinroutes, Q())
                queryset=queryset.filter(query)

            else:
                queryset=queryset.filter(stationinroute__pk=int(stationinroute))

        return queryset
