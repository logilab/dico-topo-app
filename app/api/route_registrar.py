import sqlalchemy

from math import ceil

from collections import OrderedDict, Iterable

import urllib
from copy import copy
from flask import request
from sqlalchemy import select, func, desc, asc
from sqlalchemy.exc import OperationalError

from app import JSONAPIResponseFactory, api_bp, db


class JSONAPIRouteRegistrar(object):
    """

    """

    def __init__(self, api_version, url_prefix):
        self.api_version = api_version
        self.url_prefix = url_prefix

    @staticmethod
    def count(model):
        return db.session.query(func.count('*')).select_from(model).scalar()

    @staticmethod
    def make_url(url, args):
        # recompose URL
        return url + "?" + urllib.parse.urlencode(args)

    def register_get_routes(self, obj_getter, model, facade_class):
        """

        :param model:
        :param facade_class:
        :param obj_getter:
        :param facade:
        :return:
        """

        # ================================
        # Collection resource GET route
        # ================================
        get_collection_rule = '/api/{api_version}/{type_plural}'.format(
            api_version=self.api_version,
            type_plural=facade_class.TYPE_PLURAL
        )

        def collection_endpoint():
            """
            Support filtering, sorting and pagination
            - Filtering syntax :
              filter[field_name]=searched_value
              field_name MUST be a mapped field of the underlying queried model
            - Sorting syntax :
              The sort respects the fields order :
              model.field,model.field2,model.field3...
              Sort by ASC by default, use the minus (-) operator to sort by DESC: -model.field
            - Pagination syntax requires page[number], page[size] or both parameters to be supplied in the URL:
              page[number]=1&page[size]=100
              The size cannot be greater than the limit defined in the corresponding Facade class
              If the page size is omitted, it is set to its default value (defined in the Facade class)
              If the page number is omitted, it is set to 1
              Provide self,first,last,prev,next links for the collection (top-level)
              Omit the prev link if the current page is the first one, omit the next link if it is the last one

            Return a 400 Bad Request if something goes wrong with the syntax or
             if the sort/filter criteriae are incorrect
            """
            url_prefix = request.host_url[:-1] + self.url_prefix

            links = {

            }

            objs_query = model.query
            try:
                # if request has filter parameter
                # TODO: implement some filtering operators (startswith, endswith, like...)
                #       using a filter_operator request parameter ?
                filter_criteriae = []
                filters = [(f, f[len('filter['):-1])  # (filter_param, filter_fieldname)
                           for f in request.args.keys() if f.startswith('filter[') and f.endswith(']')]
                if len(filters) > 0:
                    for filter_param, filter_fieldname in filters:
                        for criteria in request.args[filter_param].split(','):
                            new_criteria = "%s.%s=='%s'" % (model.__tablename__, filter_fieldname, criteria)
                            filter_criteriae.append(new_criteria)

                    objs_query = objs_query.filter(*filter_criteriae)

                # if request has sorting parameter
                if "sort" in request.args:
                    sort_criteriae = []
                    sort_order = asc
                    for criteria in request.args["sort"].split(','):
                        if criteria.startswith('-'):
                            sort_order = desc
                            criteria = criteria[1:]
                        sort_criteriae.append(getattr(model, criteria))
                    print("sort criteriae: ", request.args["sort"], sort_criteriae)
                    objs_query = objs_query.order_by(sort_order(*sort_criteriae))
                    # print(facade_class.get_sort_criteria('commune'))

                # if request has pagination parameters
                # add links to the top-level object
                if 'page[number]' in request.args or 'page[size]' in request.args:
                    num_page = int(request.args.get('page[number]', 1))
                    page_size = min(
                        facade_class.ITEMS_PER_PAGE,
                        int(request.args.get('page[size]', facade_class.ITEMS_PER_PAGE))
                    )

                    pagination_obj = objs_query.paginate(num_page, page_size, False)
                    all_objs = pagination_obj.items
                    args = OrderedDict(request.args)

                    count = JSONAPIRouteRegistrar.count(model)
                    nb_pages = max(1, ceil(count / page_size))

                    args["page[size]"] = page_size
                    links["self"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    args["page[number]"] = 1
                    links["first"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    args["page[number]"] = nb_pages
                    links["last"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    if num_page > 1:
                        args["page[number]"] = max(1, num_page - 1)
                        links["prev"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    if num_page < nb_pages:
                        args["page[number]"] = min(nb_pages, num_page + 1)
                        links["next"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                # else it is not paginated
                else:
                    links["self"] = request.url
                    all_objs = objs_query.all()

                facade_objs = [facade_class(url_prefix, obj) for obj in all_objs]

                return JSONAPIResponseFactory.make_data_response(
                    [obj.resource for obj in facade_objs],
                    links=links
                )

            except (AttributeError, OperationalError) as e:
                return JSONAPIResponseFactory.make_errors_response(
                    {"status": 400, "details": str(e)}, status=400
                )

        collection_endpoint.__name__ = "%s_%s" % (facade_class.TYPE_PLURAL, collection_endpoint.__name__)
        # register the rule
        api_bp.add_url_rule(get_collection_rule, endpoint=collection_endpoint.__name__, view_func=collection_endpoint)

        # =======================
        # Single resource GET route
        # =======================
        single_obj_rule = '/api/{api_version}/{type_plural}/<id>'.format(
            api_version=self.api_version,
            type_plural=facade_class.TYPE_PLURAL
        )

        def single_obj_endpoint(id):
            url_prefix = request.host_url[:-1] + self.url_prefix
            obj, kwargs, errors = obj_getter(id)
            if obj is None:
                return JSONAPIResponseFactory.make_errors_response(errors, **kwargs)
            else:
                f_placename = facade_class(url_prefix, obj)
                links = {
                    "self": request.url
                }
                return JSONAPIResponseFactory.make_data_response(f_placename.resource, links=links)

        single_obj_endpoint.__name__ = "%s_%s" % (facade_class.TYPE_PLURAL, single_obj_endpoint.__name__)
        # register the rule
        api_bp.add_url_rule(single_obj_rule, endpoint=single_obj_endpoint.__name__, view_func=single_obj_endpoint)

    def register_relationship_get_route(self, obj_getter, facade_class, rel_name):
        """
            Support Pagination syntax :
            - Pagination syntax requires page[number], page[size] or both parameters to be supplied in the URL:
              page[number]=1&page[size]=100
              The size cannot be greater than the limit defined in the corresponding Facade class
              If the page size is omitted, it is set to its default value (defined in the Facade class)
              If the page number is omitted, it is set to 1
              Provide self,first,last,prev,next links for the collection (top-level)
              Omit the prev link if the current page is the first one, omit the next link if it is the last one
        """
        # ===============================
        # Relationships self link route
        # ===============================
        rule = '/api/{api_version}/{type_plural}/<id>/relationships/{rel_name}'.format(
            api_version=self.api_version,
            type_plural=facade_class.TYPE_PLURAL, rel_name=rel_name
        )

        def resource_relationship_endpoint(id):
            url_prefix = request.host_url[:-1] + self.url_prefix
            obj, kwargs, errors = obj_getter(id)

            if obj is None:
                return JSONAPIResponseFactory.make_errors_response(errors, **kwargs)
            else:
                relationship = facade_class(url_prefix, obj).relationships[rel_name]
                data = relationship["resource_identifier_getter"]()

                links = relationship["links"]
                paginated_links = {}

                # if request has pagination parameters
                # add links to the top-level object
                if 'page[number]' in request.args or 'page[size]' in request.args:
                    num_page = int(request.args.get('page[number]', 1))
                    page_size = min(
                        facade_class.ITEMS_PER_PAGE,
                        int(request.args.get('page[size]', facade_class.ITEMS_PER_PAGE))
                    )

                    args = OrderedDict(request.args)
                    count = len(data)
                    nb_pages = max(1, ceil(count / page_size))

                    args["page[size]"] = page_size
                    paginated_links["self"] = JSONAPIRouteRegistrar.make_url(links["self"], args)
                    paginated_links["related"] = JSONAPIRouteRegistrar.make_url(links["related"], args)
                    args["page[number]"] = 1
                    paginated_links["first"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    args["page[number]"] = nb_pages
                    paginated_links["last"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    if num_page > 1:
                        args["page[number]"] = max(1, num_page - 1)
                        paginated_links["prev"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    if num_page < nb_pages:
                        args["page[number]"] = min(nb_pages, num_page + 1)
                        paginated_links["next"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)

                    # perform the pagination
                    data = data[(num_page - 1) * page_size:min(num_page * page_size, count)]
                    links.update(paginated_links)

                data = {
                    "links": links,
                    "data": data
                }
                return JSONAPIResponseFactory.make_response(data, **kwargs)

        resource_relationship_endpoint.__name__ = "%s_%s_%s" % (
            facade_class.TYPE_PLURAL, rel_name.replace("-", "_"), resource_relationship_endpoint.__name__
        )
        # register the rule
        api_bp.add_url_rule(rule, endpoint=resource_relationship_endpoint.__name__,
                            view_func=resource_relationship_endpoint)

        # ===================================
        # Relationships related link route
        # ===================================
        rule = '/api/{api_version}/{type_plural}/<id>/{rel_name}'.format(
            api_version=self.api_version,
            type_plural=facade_class.TYPE_PLURAL, rel_name=rel_name
        )

        def resource_endpoint(id):
            """
                Support Pagination syntax :
                - Pagination syntax requires page[number], page[size] or both parameters to be supplied in the URL:
                  page[number]=1&page[size]=100
                  The size cannot be greater than the limit defined in the corresponding Facade class
                  If the page size is omitted, it is set to its default value (defined in the Facade class)
                  If the page number is omitted, it is set to 1
                  Provide self,first,last,prev,next links for the collection (top-level)
                  Omit the prev link if the current page is the first one, omit the next link if it is the last one
            """
            url_prefix = request.host_url[:-1] + self.url_prefix
            obj, kwargs, errors = obj_getter(id)
            if obj is None:
                return JSONAPIResponseFactory.make_errors_response(errors, **kwargs)
            else:
                relationship = facade_class(url_prefix, obj).relationships[rel_name]
                resource_data = relationship["resource_getter"]()

                paginated_links = {}
                links = {
                    "self": request.url
                }

                # if request has pagination parameters
                # add links to the top-level object
                if 'page[number]' in request.args or 'page[size]' in request.args:
                    num_page = int(request.args.get('page[number]', 1))
                    page_size = min(
                        facade_class.ITEMS_PER_PAGE,
                        int(request.args.get('page[size]', facade_class.ITEMS_PER_PAGE))
                    )

                    args = OrderedDict(request.args)
                    count = len(resource_data)
                    nb_pages = max(1, ceil(count / page_size))

                    args["page[size]"] = page_size
                    paginated_links["self"] = JSONAPIRouteRegistrar.make_url(links["self"], args)
                    args["page[number]"] = 1
                    paginated_links["first"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    args["page[number]"] = nb_pages
                    paginated_links["last"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    if num_page > 1:
                        args["page[number]"] = max(1, num_page - 1)
                        paginated_links["prev"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)
                    if num_page < nb_pages:
                        args["page[number]"] = min(nb_pages, num_page + 1)
                        paginated_links["next"] = JSONAPIRouteRegistrar.make_url(request.base_url, args)

                    # perform the pagination
                    resource_data = resource_data[(num_page - 1) * page_size:min(num_page * page_size, count)]
                    links.update(paginated_links)

                return JSONAPIResponseFactory.make_data_response(resource_data, links=links)

        resource_endpoint.__name__ = "%s_%s_%s" % (
            facade_class.TYPE_PLURAL, rel_name.replace("-", "_"), resource_endpoint.__name__
        )
        # register the rule
        api_bp.add_url_rule(rule, endpoint=resource_endpoint.__name__, view_func=resource_endpoint)
