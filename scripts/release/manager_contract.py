"""One canonical ASCII JSON wire format for the stock-compatible manager envelope."""
import json


def require(value,message):
    if not value:raise ValueError(message)


def wire(value):
    if isinstance(value,dict):
        require(all(isinstance(key,str) for key in value),'non-string JSON key')
        for key,item in value.items():wire(key);wire(item)
    elif isinstance(value,list):
        for item in value:wire(item)
    elif isinstance(value,str):
        require(all(32<=ord(char)<=126 or char in '\n\t\r' for char in value),'noncanonical JSON string')
    elif type(value) is int:require(0<=value<=9007199254740991,'noncanonical integer')
    elif type(value) is bool:pass
    else:raise ValueError('unsupported canonical JSON value')


def canonical(value):
    wire(value)
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode('ascii')


def decode(raw):
    require(isinstance(raw,bytes) and len(raw)<=1024*1024,'canonical document bound exceeded')
    def pairs(items):
        output={}
        for key,value in items:
            require(key not in output,'duplicate JSON key');output[key]=value
        return output
    value=json.loads(raw.decode('ascii'),object_pairs_hook=pairs,
                     parse_float=lambda value: (_ for _ in ()).throw(ValueError('floating point JSON refused')),
                     parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON refused')))
    require(canonical(value)==raw,'document is not canonical compact JSON with one newline')
    return value
