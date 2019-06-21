config-merger
=============

A 'Commandline utility' and 'Pyhton module' for merging nested dictionary/list structures from a variety of formats.

Simple Concept Example
```
    input1
        {'a': 1, 'b': 2}
    input2
        {'b': 4, 'c': 3}
    output
        {'a': 1, 'b': 4, 'c': 3}
```

The input data sources can be:

* .json
* .yaml (with pyyaml installed)
* .py
* http endpoints (emitting json)

Other keys can be replaced as variables in strings

Complex Example
```
    input1.json
        {"a": 1, "b": [2], "c": {"d": "a is ${a}"}}
    input2.yaml
        a: 5
        b:
            - 999
    output
        {"a": 5, "b": [2, 999], "c": {"d": "a is 5"}}
```


Examples
--------

### Commandline use ###

Positional arguments are input data that are

* paths to data files (`json`, `yaml`, `py`)
* urls
* a `json` string

Inputs must all have a 'Top Level' dictionary.

Output is to `stdout` and defaults to `python.pprint`

```bash
    python3 config_merger.py \
        test_data.py \
        "http://herald.development.int.thisisglobal.com/api/thea/services/112/" \
        ../at/at_suite1/robot_runner.config.json \
        ../at/at_suite1/variables/_default.yaml \
        '{"a": 1, "b": 2}' \
        --format json \
        > \
        my_config.json
```

### Python Module use ###

```python
    from config_merger import merge
    data = merge('{"a": 1}', {'b': 2}, 'http://myapi.net/data.json', '/file/path/data.yaml')
    print(data)
```


Example Usecases
----------------

* Config is stored in a remote api and we want to overlay local settings at system startup
* multiple `docker-compose.yml` can be combined
* Production has some private key overrides that need to be applied to config for production


TODO
----

* More MERGE-META
    * Implement merge modes for dicts
    * Allow meta to propergate to child dicts (will need copy behaviour of options)
* Other formats could be supported (`xml`?)
* Outputting as a `py` file could be useful (we would have to construct this as a string manually)
* Allow `merge(open('filename.json'))`. We currently don't support this as we have no way of knowing the file format to parse the data in the file.
* input from pipe?
