import concurrent.futures
import json
import queue
import time

from jira import JIRA as JIRABase
from jira.client import translate_resource_args
from jira.utils import json_loads
from requests.adapters import HTTPAdapter


class CustomFieldType:
    CASCADING_SELECT = 'com.atlassian.jira.plugin.system.customfieldtypes:cascadingselect'
    DATE_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:datepicker'
    DATE_TIME = 'com.atlassian.jira.plugin.system.customfieldtypes:datetime'
    FLOAT = 'com.atlassian.jira.plugin.system.customfieldtypes:float'
    GROUP_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:grouppicker'
    IMPORT_ID = 'com.atlassian.jira.plugin.system.customfieldtypes:importid'
    LABELS = 'com.atlassian.jira.plugin.system.customfieldtypes:labels'
    MULTI_CHECK_BOXES = 'com.atlassian.jira.plugin.system.customfieldtypes:multicheckboxes'
    MULTI_GROUP_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:multigrouppicker'
    MULTI_SELECT = 'com.atlassian.jira.plugin.system.customfieldtypes:multiselect'
    MULTI_USER_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker'
    MULTI_VERSION = 'com.atlassian.jira.plugin.system.customfieldtypes:multiversion'
    PROJECT = 'com.atlassian.jira.plugin.system.customfieldtypes:project'
    RADIO_BUTTONS = 'com.atlassian.jira.plugin.system.customfieldtypes:radiobuttons'
    READ_ONLY_FIELD = 'com.atlassian.jira.plugin.system.customfieldtypes:readonlyfield'
    SELECT = 'com.atlassian.jira.plugin.system.customfieldtypes:select'
    TEXT_AREA = 'com.atlassian.jira.plugin.system.customfieldtypes:textarea'
    TEXT_FIELD = 'com.atlassian.jira.plugin.system.customfieldtypes:textfield'
    URL = 'com.atlassian.jira.plugin.system.customfieldtypes:url'
    USER_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:userpicker'
    VERSION = 'com.atlassian.jira.plugin.system.customfieldtypes:version'


class CustomFieldSearcherKey:
    CASCADING_SELECT = 'com.atlassian.jira.plugin.system.customfieldtypes:cascadingselectsearcher'
    DATE_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:daterange'
    DATE_TIME = 'com.atlassian.jira.plugin.system.customfieldtypes:datetimerange'
    FLOAT = 'com.atlassian.jira.plugin.system.customfieldtypes:exactnumber'
    GROUP_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:grouppickersearcher'
    IMPORT_ID = 'com.atlassian.jira.plugin.system.customfieldtypes:exactnumber'
    LABELS = 'com.atlassian.jira.plugin.system.customfieldtypes:labelsearcher'
    MULTI_CHECK_BOXES = 'com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher'
    MULTI_GROUP_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher'
    MULTI_SELECT = 'com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher'
    MULTI_USER_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:userpickergroupsearcher'
    MULTI_VERSION = 'com.atlassian.jira.plugin.system.customfieldtypes:versionsearcher'
    PROJECT = 'com.atlassian.jira.plugin.system.customfieldtypes:projectsearcher'
    RADIO_BUTTONS = 'com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher'
    READ_ONLY_FIELD = 'com.atlassian.jira.plugin.system.customfieldtypes:textsearcher'
    SELECT = 'com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher'
    TEXT_AREA = 'com.atlassian.jira.plugin.system.customfieldtypes:textsearcher'
    TEXT_FIELD = 'com.atlassian.jira.plugin.system.customfieldtypes:textsearcher'
    URL = 'com.atlassian.jira.plugin.system.customfieldtypes:exacttextsearcher'
    USER_PICKER = 'com.atlassian.jira.plugin.system.customfieldtypes:userpickergroupsearcher'
    VERSION = 'com.atlassian.jira.plugin.system.customfieldtypes:versionsearcher'


class JIRA(JIRABase):
    REQUEST_LIMIT = 1000
    REQUEST_WORKERS = 10
    REQUEST_MAX_RETRIES = 20
    REQUEST_RETRY_INTERVAL = 10
    FIELD_OPTIONS_LIMIT = 10000

    def __init__(self, *args, **kwargs):
        # Patch requests to change the size of the connection pool
        super().__init__(*args, **kwargs)
        adapter = HTTPAdapter(pool_connections=JIRA.REQUEST_WORKERS, pool_maxsize=JIRA.REQUEST_WORKERS)
        self._session.mount('http://', adapter)
        self._session.mount('https://', adapter)

    @translate_resource_args
    def transitions(self, issue, id=None, expand=None):
        params = {}
        if id is not None:
            params['transitionId'] = id
        if expand is not None:
            params['expand'] = expand
        params['skipRemoteOnlyCondition'] = True
        params['includeUnavailableTransitions'] = True
        response = self._get_json(f'issue/{issue}/transitions', params=params)['transitions']
        return response

    def find_transitionid_by_name(self, issue, name, **kwargs):
        for transition in self.transitions(issue, **kwargs):
            if transition['name'].lower() == name.lower():
                return transition['id']
        return None

    def create_custom_field(self, name=None, description=None, type=None, searcherKey=None):
        data = {}
        if name is not None:
            data['name'] = name
        if description is not None:
            data['description'] = description
        if type is not None:
            data['type'] = type
        if searcherKey is not None:
            data['searcherKey'] = searcherKey
        url = self._get_url('field')
        response = self._session.post(url, data=json.dumps(data))
        return json_loads(response)

    def set_custom_field_options(self, field, options):
        context = self._get_custom_field_context(field)
        if not context:
            raise ValueError('Custom field context not found')
        self._delete_custom_field_options(field, context)
        if isinstance(options, list):
            return self._create_custom_field_options(field, context, options)
        if isinstance(options, dict):
            return self._create_custom_field_options_cascading(field, context, options)
        raise TypeError('\'options\' must be a list or a dict')

    def clear_custom_field_options(self, field):
        context = self._get_custom_field_context(field)
        if not context:
            raise ValueError('Custom field context not found')
        self._delete_custom_field_options(field, context)

    def _get_custom_field_context(self, field):
        url = self._get_url(f'field/{field}/context')
        response = json_loads(self._session.get(url))
        if 'values' not in response:
            return None
        return response['values'][0]['id']

    def _create_custom_field_options(self, field, context, options):
        data = [{'value': option} for option in set(options)]
        response = self._create_all_custom_field_options(field, context, data)

        # Reorder options
        order = self._sort_fields(options, response['options'])
        self._reorder_all_custom_field_options(field, context, order)

        return response

    def _create_custom_field_options_cascading(self, field, context, options):
        # Create parent options
        options = {key: value for key, value in options.items() if value}
        data = [{'value': option} for option in set(options.keys())]
        parents = self._create_all_custom_field_options(field, context, data)['options']
        parent_ids = {option['value']: option['id'] for option in parents}

        # Create child options
        data = []
        for parent_value, child_options in options.items():
            parent_id = parent_ids.get(parent_value)
            data.extend([{'value': option, 'optionId': parent_id} for option in set(child_options)])
        children = self._create_all_custom_field_options(field, context, data)['options']

        # Reorder options
        def reorder_worker(order):
            self._reorder_all_custom_field_options(field, context, order)
        parent_order, child_orders = self._sort_fields_cascading(options, parents, children)
        self._reorder_all_custom_field_options(field, context, parent_order)
        with concurrent.futures.ThreadPoolExecutor(JIRA.REQUEST_WORKERS) as executor:
            executor.map(reorder_worker, child_orders)

        return {'options': parents + children}

    def _delete_custom_field_options(self, field, context):
        def get_total():
            url = self._get_url(f'field/{field}/context/{context}/option')
            response = json_loads(self._session.get(url))
            return len(response['values'])

        while get_total() > 0:
            start_at = 0
            parent_options = []
            child_options = []
            while True:
                url = self._get_url(f'field/{field}/context/{context}/option?startAt={start_at}')
                response = json_loads(self._session.get(url))
                start_at += len(response['values'])
                for option in response['values']:
                    if 'optionId' not in option:
                        parent_options.append(option['id'])
                    else:
                        child_options.append(option['id'])
                if response['isLast']:
                    break

            def delete_worker(option):
                tries = 1
                while tries <= JIRA.REQUEST_MAX_RETRIES:
                    try:
                        option_url = self._get_url(f'field/{field}/context/{context}/option/{option}')
                        self._session.delete(option_url)
                        break
                    except Exception as exc:
                        tries += 1
                        if tries > JIRA.REQUEST_MAX_RETRIES:
                            raise exc from None
                        time.sleep(JIRA.REQUEST_RETRY_INTERVAL)
            with concurrent.futures.ThreadPoolExecutor(JIRA.REQUEST_WORKERS) as executor:
                executor.map(delete_worker, child_options)
            with concurrent.futures.ThreadPoolExecutor(JIRA.REQUEST_WORKERS) as executor:
                executor.map(delete_worker, parent_options)

    def _create_all_custom_field_options(self, field, context, options):
        url = self._get_url(f'field/{field}/context/{context}/option')
        limit = JIRA.REQUEST_LIMIT
        result = {'options': []}
        options = options[:JIRA.FIELD_OPTIONS_LIMIT]
        while options:
            data = {'options': options[:limit]}
            options = options[limit:]

            tries = 1
            while tries <= JIRA.REQUEST_MAX_RETRIES:
                try:
                    result['options'].extend(json_loads(self._session.post(url, data=json.dumps(data)))['options'])
                    break
                except Exception as exc:
                    tries += 1
                    if tries > JIRA.REQUEST_MAX_RETRIES:
                        raise exc from None
                    time.sleep(JIRA.REQUEST_RETRY_INTERVAL)
        return result

    def _reorder_all_custom_field_options(self, field, context, order):
        url = self._get_url(f'field/{field}/context/{context}/option/move')
        limit = JIRA.REQUEST_LIMIT
        while order:
            data = {'position': 'Last', 'customFieldOptionIds': order[:limit]}
            order = order[limit:]

            tries = 1
            while tries <= JIRA.REQUEST_MAX_RETRIES:
                try:
                    self._session.put(url, data=json.dumps(data))
                    break
                except Exception as exc:
                    tries += 1
                    if tries > JIRA.REQUEST_MAX_RETRIES:
                        raise exc from None
                    time.sleep(JIRA.REQUEST_RETRY_INTERVAL)

    def _sort_fields(self, options, response):
        option_ids = {option['value']: option['id'] for option in response}
        return [option_ids[option] for option in options]

    def _sort_fields_cascading(self, options, parents, children):
        option_ids = {option['value']: {'id': option['id'], 'children': {}} for option in parents}
        parent_names = {option['id']: option['value'] for option in parents}
        parent_order = [option_ids[option]['id'] for option in options.keys()]
        child_orders = []
        for child in children:
            option_ids[parent_names[child['optionId']]]['children'][child['value']] = child['id']
        for parent, children in options.items():
            children_ids = option_ids[parent]['children']
            child_orders.append([children_ids[child] for child in children])
        return parent_order, child_orders
