#!/usr/bin/env python3
## -*- coding: utf-8 -*-

import re
import json
from pprint import pformat
from functools import reduce, lru_cache
import collections
import urllib
import urllib.request
import logging
import os.path
import io

try:
    import yaml
except ImportError:
    def _raise_pyyaml_not_installed(*args, **kwargs):
        raise Exception('pyyaml not installed')
    class yaml():
        load = _raise_pyyaml_not_installed
        dump = _raise_pyyaml_not_installed


log = logging.getLogger(__name__)

VERSION = '0.0'

REGEX_EXTENSION = re.compile(r'.*?\.([^.?/]+)(?:\?|$)')
REGEX_TEMPLATE_VAR = re.compile(r'\${(.+?)}')
TEMPLATE_REPLACEMENT = '${%s}'

DEFAULT_OUTPUT_FORMAT = 'pformat'

DEFAULT_METADATA_KEY = '__CONFIG-MERGER-META__'
META_funcs = {
    'combine': {
        'concat': lambda a, b: a + b,
        'keep': lambda a, b: a,
        'replace': lambda a, b: b,
        'and': lambda a, b: set(a) & set(b),
        'or': lambda a, b: set(a) | set(b),
    },
}

# Utils ------------------------------------------------------------------------

def _postmortem(func, *args, **kwargs):
    import traceback
    import pdb
    import sys
    try:
        return func(*args, **kwargs)
    except Exception:
        type, value, tb = sys.exc_info()
        traceback.print_exc()
        pdb.post_mortem(tb)


# Core Merging -----------------------------------------------------------------

def update_dict_subkeys(d, u, none_values_are_transparent=False, **options):
    """
    Merge two dicts, ensuring subkeys are respected
    (rather than .update, that just tumps the top dictionary)
    The first argument dict is modified in-place and is the return value.
    Lists under the same key are concatenated

    Inspired by: https://stackoverflow.com/a/3233356/3356840

    >>> data = {'a': 1, 'b': [2], 'c': {'d': 4, 'e': 5, 'f': {'g': 7}}}
    >>> update_dict_subkeys(data, {'c': {'d': 999}})
    {'a': 1, 'b': [2], 'c': {'d': 999, 'e': 5, 'f': {'g': 7}}}
    >>> update_dict_subkeys(data, {'h': 8})
    {'a': 1, 'b': [2], 'c': {'d': 999, 'e': 5, 'f': {'g': 7}}, 'h': 8}
    >>> update_dict_subkeys(data, {'b': [777, {'i': 9}]})
    {'a': 1, 'b': [2, 777, {'i': 9}], 'c': {'d': 999, 'e': 5, 'f': {'g': 7}}, 'h': 8}

    >>> update_dict_subkeys({'a': 1}, {'a': None})
    {'a': None}
    >>> update_dict_subkeys({'a': 1}, {'a': None}, none_values_are_transparent=True)
    {'a': 1}

    >>> update_dict_subkeys({'a': [1, 2, 3]}, {'a': [3, 4, 5], '__CONFIG-MERGER-META__': {'a': 'and'}})
    {'a': {3}}
    >>> update_dict_subkeys({'a': [1, 2, 3]}, {'a': [3, 4, 5], '__CONFIG-MERGER-META__': {'b': 'and'}})
    {'a': [1, 2, 3, 3, 4, 5]}
    >>> update_dict_subkeys({'a': [1, 2, 3]}, {'a': [3, 4, 5], '__CONFIG-MERGER-META__': {'a': 'keep'}})
    {'a': [1, 2, 3]}
    >>> update_dict_subkeys({'a': [1, 2, 3]}, {'a': [3, 4, 5], '__CONFIG-MERGER-META__': {'a': 'replace'}})
    {'a': [3, 4, 5]}


    """
    metadata_key = options.get('metadata_key', DEFAULT_METADATA_KEY)
    meta = {
        # TODO: .pop() is probably a mistake long term - we should have a REMOVE-META sweep at the end
        **d.pop(metadata_key, {}),
        **u.pop(metadata_key, {}),
    }

    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            # TODO: meta[replace]
            # Recursively merge subdicts
            d[k] = update_dict_subkeys(d.get(k, {}), v, **options)
        elif isinstance(v, collections.abc.Iterable) and not isinstance(v, str):
            # Combine lists
            d[k] = META_funcs['combine'][meta.get(k, 'concat')](d.get(k, []), v)
        else:
            # Option: skip `None` values
            if none_values_are_transparent and k in d and u[k] == None:
                continue
            # Overwrite or create key
            d[k] = u[k]
    return d


def replace_template_variables(data, flat_parent_replacements={}, **options):
    """
    >>> replace_template_variables({'a': 'XtestX', 'b': 'template ${a}'})
    {'a': 'XtestX', 'b': 'template XtestX'}
    >>> replace_template_variables({'a': '1', 'b': '2', 'c': {'d': '${a}x${b}'}})
    {'a': '1', 'b': '2', 'c': {'d': '1x2'}}
    """
    data_chain = collections.ChainMap(data, flat_parent_replacements)

    def _replace_template(value):
        if isinstance(value, str):
            for token in REGEX_TEMPLATE_VAR.findall(value):
                value = value.replace(TEMPLATE_REPLACEMENT % token, str(data_chain.get(token, '')))
        return value

    for k, v in data.items():
        if isinstance(v, str):
            data[k] = _replace_template(v)
        elif isinstance(v, collections.abc.Mapping):
            replace_template_variables(v, data_chain)
        elif isinstance(v, collections.abc.Iterable) and not isinstance(v, str):
            v[:] = list(map(lambda i: _replace_template(i), v))
        #else:
        #    raise Exception('unknown type')

    return data


# Input Formats --------------------------------------------------------------------------------


def parse_py(filehandle):
    import sys
    import importlib
    from pathlib import Path
    path = Path(filehandle.name)
    sys.path.append(str(path.parent))
    module = importlib.import_module(path.stem)
    return {k: v for k, v in vars(module).items() if not k.startswith('_')}

def parse_env(filehandle):
    """
    >>> from io import StringIO
    >>> input = StringIO()
    >>> input.write('a=1\\nb=2\\n')
    8
    >>> input.seek(0)
    0
    >>> parse_env(input)
    {'a': '1', 'b': '2'}
    """
    def _parse_env_line(acc, line):
        line = line.lstrip()
        if not line.startswith('#'):
            line_split = line.split('=', maxsplit=1)
            if len(line_split) == 2:
                key, value = line_split
                acc[key] = value
        return acc
    return reduce(_parse_env_line, filehandle.read().split('\n'), {})

INPUT_FORMATS = {
    'py': parse_py,
    'json': json.load,
    'yaml': yaml.safe_load,
    'env': parse_env,
}


# Output Formats -------------------------------------------------------------------------------

def output_py(data):
    raise NotImplementedError()

def output_env(data):
    """
    >>> output_env({'a':1, 'b':2})
    'a=1\\nb=2\\n'
    """
    from io import StringIO
    output = StringIO()
    for key, value in data.items():
        output.write(f'{key}={value}\n')
    output.seek(0)
    return output.read()

OUTPUT_FORMATS = {
    'pformat': pformat,
    'py': output_py,
    'json': json.dumps,
    'yaml': yaml.dump,
    'env': output_env,
}


# Parse Inputs ---------------------------------------------------------------------------------

def _load_data_from_source(source):
    '''
    >>> _load_data_from_source('{"a": 1}')
    {'a': 1}

    #TODO: mock url and file opening to assert this?
    '''
    assert source
    #if not isinstance(source, dict) and isinstance(source, collections.abc.Mapping):
    #    # TODO: Convert to hard dict
    #    raise Exception()
    #if isinstance(source, dict) or (hasattr(source, 'keys') and hasattr(source, '__getitem__')):
    if isinstance(source, collections.abc.Mapping):
        return source
    def is_url(source):
        try:
            return urllib.parse.urlparse(source).scheme != ''
        except Exception:
            return False
    if isinstance(source, str) and source.startswith('{') and source.endswith('}'):
        return json.loads(source)
    ext = 'unknown'
    if is_url(source):
        ext = 'json'  # hack: assume all web urls are json based
    try:
        ext = REGEX_EXTENSION.match(source).group(1)
    except AttributeError:
        pass
    if ext not in INPUT_FORMATS:
        raise Exception(f'{ext} is not a supported input format for {source}')
        #return {}

    def resolve_opener(source):
        if isinstance(source, io.IOBase):
            raise NotImplementedError('filehandles are currently unsupported as we dont have a mechanism for passing a format')
        if os.path.exists(source):
            return open(source, 'rt')
        if is_url(source):
            return urllib.request.urlopen(source)
        raise Exception(f'Unknown source {source}')

    with resolve_opener(source) as filehandle:
        data = INPUT_FORMATS[ext](filehandle)
        assert isinstance(data, collections.Mapping), f'Top Level of data must be a dict - {source}'
        return data


# Top Level Data Processing ---------------------------------------------------

def _reduce_data_sources(*sources, **options):
    return reduce(
        lambda acc, source: update_dict_subkeys(acc, _load_data_from_source(source), **options),
        sources,
        {},
    )

def _format_output(sources, format=DEFAULT_OUTPUT_FORMAT, **options):
    return OUTPUT_FORMATS[format](merge(*sources, **options))


# Folder Tree Merging Operations -----------------------------------------------

class VariableOverlay():
    """
    TODO: describe the use of this class
    Folder structures and example usecase
    """

    DEFAULT_FILENAME = '_default'

    def __init__(self, path):
        self._path = path

    @lru_cache(maxsize=128)
    def _get_data(self, path, name):
        """
        """
        # TODO: listdir and get all files with extensions to support multiple possible extensions
        filename = os.path.join(self._path, path, f'{name}.json')
        if os.path.exists(filename):
            return _load_data_from_source(filename)
        return {}

    @lru_cache(maxsize=128)
    def get(self, names=(), include_sub_folders=()):
        """
        _path/name1
        _path/name2
        _path/include_sub_folders1/name1
        _path/include_sub_folders1/name2
        _path/include_sub_folders2/name1
        _path/include_sub_folders2/name2
        """
        if isinstance(names, str):
            names = (names, )
        if isinstance(include_sub_folders, str):
            include_sub_folders = (include_sub_folders, )
        CURRENT_FOLDER = ('', )
        include_sub_folders = CURRENT_FOLDER + tuple(include_sub_folders)
        names = (self.DEFAULT_FILENAME, ) + tuple(names)

        data = {}
        for folder in include_sub_folders:
            for name in names:
                update_dict_subkeys(data, self._get_data(folder, name))

        # TODO: perform variable replacement here?

        return data


# Public Python API -----------------------------------------------------------

def merge(*sources, **options):
    '''
    >>> merge(
    ...     '{"a": 1, "b": [2], "c": {"d": "a is ${a}"}}',
    ...     {'a': 5, 'b': [999]},
    ... )
    {'a': 5, 'b': [2, 999], 'c': {'d': 'a is 5'}}
    >>> merge({'a': 1}, {'a': None}, none_values_are_transparent=True, another_option='TEST')
    {'a': 1}
    '''
    return replace_template_variables(_reduce_data_sources(*sources, **options), **options)


# Command Line ----------------------------------------------------------------

def get_args():
    import argparse
    parser = argparse.ArgumentParser(
        description=f'''{__name__} {VERSION}

        Example Useage:
            {__name__} data1.json data2.yaml "http://source.com/data3.json"

        Tests:
            pytest --doctest-modules config_merger.py --pdb

        ''',
        epilog=''''''
    )

    parser.add_argument('sources', nargs='+', help='input files')

    parser.add_argument('--format', action='store', choices=OUTPUT_FORMATS.keys(), help='output format', default=DEFAULT_OUTPUT_FORMAT)
    parser.add_argument('--python_path', action='append', help='paths', default=[])

    parser.add_argument('--none_values_are_transparent', action='store_true', help='Empty/Null/None values will not override set values', default=False)
    parser.add_argument('--metadata_key', action='store', help='', default=DEFAULT_METADATA_KEY)

    parser.add_argument('-v', '--verbose', action='store_true', help='', default=False)
    parser.add_argument('--postmortem', action='store_true', help='Automatically drop into pdb shell on exception. Used for debuging')
    parser.add_argument('--version', action='version', version=VERSION)

    args = vars(parser.parse_args())
    return args


if __name__ == "__main__":
    args = get_args()
    logging.basicConfig(level=logging.DEBUG if args['verbose'] else logging.INFO)

    def main(sources=(), python_path=(), **args):
        for path in python_path:
            if not os.path.isdir(path):
                raise Exception(f'python_path does not exist {path}')
            sys.path.append(path)
        print(_format_output(sources, **args))

    if args.get('postmortem'):
        _postmortem(main, **args)
    else:
        main(**args)
