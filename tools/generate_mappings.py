import json

from miio import AirHumidifierMiot
from miio.integrations.zhimi.humidifier.airhumidifier_miot import SMARTMI_EVAPORATIVE_HUMIDIFIER_2

result = {
    "meta": {
        "driver": "python-miio",
        "title": {
            "en": "AirHumidifier"
        }
    }
}
# wget https://miot-spec.org/miot-spec-v2/instance?type=urn:miot-spec-v2:device:humidifier:0000A00E:zhimi-ca4:2
with open('../configs/instance.json') as f:
    instance = json.load(f)

unit_mapping = {
    'none': None,
    'percentage': '%',
    'celsius': 'deg C',
    'seconds': 's'
}

format_mapping = {
    'string': 'text',
    'bool': 'switch',
    'uint8': 'value',
    'int32': 'value',
    'uint32': 'value',
    'float': 'value'
}

iid_mapping = {
    (v['siid'], v['piid']): k for k,v in AirHumidifierMiot._mappings[SMARTMI_EVAPORATIVE_HUMIDIFIER_2].items()
}


for s in instance['services']:
    siid = s['iid']
    for p in s['properties']:
        piid = p['iid']
        name = iid_mapping.get((siid, piid))
        if name is None:
            # print(p['type'])
            continue

        item = {
            'title': {
                'en': name,
            },
            "order": 1,
        }
        if 'write' not in p['access']:
            item['readonly'] = True
        unit = p.get('unit')
        if unit is not None:
            if unit_mapping[unit] is not None:
                item['units'] = unit_mapping[unit]

        ranges = p.get('value-range')
        step = 1
        if ranges is not None:
            step = ranges[2]
            item['min'] = ranges[0]
            item['max'] = ranges[1]
            if step > 1:
                item['precision'] = step

        stype = p['format']
        if stype == 'float':
            item['precision'] = step
        item['type'] = format_mapping[stype]
        if ranges is not None and 'write' in p['access']:
            item['type'] = 'range'
        if item['type'] == 'value':
            item.pop('min', None)
            item.pop('max', None)

        enums = p.get('value-list')
        if enums is not None:
            item.pop('units', None)
            enum_dict = {}
            for i in enums:
                enum_dict[str(i['value'])] = {'en': i['description']}
            item['enum'] = enum_dict

        result[name] = item


extra_patch = {
    'humidity': {'units': '%, RH'},
    'target_humidity': {'units': '%, RH'},
    'use_time': {'units': 'h'},
    'power_time': {'units': 'h'},
    'actual_speed': {'units': 'rpm'},
    'speed_level': {'units': 'rpm'},
}
for k,v in extra_patch.items():
    for k2, v2 in v.items():
        result[k][k2] = v2

with open('../configs/meta_topics.json', 'w') as f:
    json.dump(result, f, indent=4)
