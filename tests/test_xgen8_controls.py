# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import json
import re
from datetime import datetime, timezone
from unittest import mock
import requests
from os.path import join
from os.path import dirname

from ultrasync import UltraSync
from ultrasync.common import NX595EVendor

# Disable logging for a cleaner testing output
import logging
logging.disable(logging.CRITICAL)

# The xGen8 NXG-8-Z-BO capture is reused, with zone and area bits added
ULTRASYNC_TEST_VAR_DIR = \
    join(dirname(__file__), 'var', NX595EVendor.XGEN8, 'nxg8zbo')

# Zone 1 is bypassed (bank 3) and zone 2 has its chime on (bank 9)
ZONE_STATUS = [
    'D405', '0000', '0000', '0100', '0000', '0000', '0000', '0000',
    '0000', '0200', '0000', '0040', '0000', '0000', '0000', '0000',
    '0100', '0000',
]

# Area 1 has night mode (byte 26) and walk test (byte 27) set
AREA_STATUS = '00' * 26 + '01' + '01' + '00' * 12

# Two events as the xGen8 panel returns them from /user/api/events.cgi
EVENTS = {
    'time': '6AEF4DE5', 'max': 1280, 'oldest': 256, 'last': 732,
    'size': [1280, 1280, 1280, 5376],
    'events': [
        {
            'id': 732, 'time': 1794065205, 'offset': 120, 'type': 'event',
            'class': 3, 'event_id': 6, 'area': 1, 'user': 4,
            'sensor': 12, 'cid_alarm': '570', 'cid_group': '01',
            'text': 'Bypass*%20%20MC%20BEDROOM%20%20HOME%20%20HA%20%20'
                    'Time%3A%2005%3A26%3A45%20PM%20%20'
                    'Date%3A%207%20Nov%202026',
        },
        {
            'id': 731, 'time': 1794050489, 'offset': 120,
            'type': 'restore', 'class': 2, 'event_id': 86, 'area': 1,
            'user': 4,
            'cid_alarm': '401', 'cid_group': '00',
            'text': 'Turn%20On%20Away*%20%20HOME%20%20HA%20%20'
                    'Time%3A%2001%3A21%3A29%20PM%20%20'
                    'Date%3A%207%20Nov%202026',
        },
    ],
}


def _response(content):
    """Return a mocked HTTP response carrying the content given."""
    obj = mock.Mock()
    obj.content = content
    obj.status_code = requests.codes.ok
    return obj


def _page(name, var, value):
    """Return a capture page with one JavaScript array replaced."""
    with open(join(ULTRASYNC_TEST_VAR_DIR, name), 'rb') as f:
        page = f.read().decode('utf-8')
    page = re.sub(
        r'var {}\s*=\s*\[[^\]]*\];'.format(var),
        'var {} = {};'.format(var, json.dumps(value)), page)
    return page.encode('utf-8')


def _login(mock_post, area_status=AREA_STATUS):
    """Log in to the mocked panel and return the UltraSync object."""
    mock_post.side_effect = (
        _response(_page('area.htm', 'areaStatus', [area_status])),
        _response(_page('zones.htm', 'zoneStatus', ZONE_STATUS)))

    uobj = UltraSync()
    assert uobj.login()
    assert uobj.vendor is NX595EVendor.XGEN8
    mock_post.reset_mock()
    return uobj


@mock.patch('requests.Session.post')
def test_xgen8_area_night_and_walk_test(mock_post):
    """
    Night mode and walk test are reported for each area
    """
    uobj = _login(mock_post)

    assert uobj.areas[0]['states']['night'] is True
    assert uobj.areas[0]['states']['walk_test'] is True


@mock.patch('requests.Session.post')
def test_xgen8_zone_bypass_and_chime_state(mock_post):
    """
    The bypass and chime state of each zone is reported
    """
    uobj = _login(mock_post)

    assert uobj.zones[0]['bypassed'] is True
    assert uobj.zones[0]['chime'] is False

    assert uobj.zones[1]['bypassed'] is False
    assert uobj.zones[1]['chime'] is True

    # Bypass and chime are not conditions, so they are not in the flags
    assert uobj.zones[0]['flags'] == []
    assert uobj.zones[1]['flags'] == []


@mock.patch('requests.Session.post')
def test_xgen8_zone_chime_command(mock_post):
    """
    The zone chime command is the one the panel's own page sends
    """
    uobj = _login(mock_post)
    mock_post.side_effect = None
    # The panel answers a zone command with the zone bank it changed
    mock_post.return_value = _response(
        b'{"time":"62BC255D","zbank":9,"zseq":76,"bankstates":"0200",'
        b' "system":[]}')

    assert uobj.set_zone_chime(zone=2, state=True) is True
    url = mock_post.call_args[0][0]
    data = mock_post.call_args[1]['data']
    assert url == 'http://zerowire/user/zonefunction.cgi'
    assert data['cmd'] == 6
    assert data['opt'] == 1
    assert data['zone'] == 1

    # An unknown zone sends nothing
    mock_post.reset_mock()
    assert uobj.set_zone_chime(zone=99, state=True) is False
    assert mock_post.call_count == 0


@mock.patch('requests.Session.post')
def test_xgen8_area_chime_command(mock_post):
    """
    The area chime is turned on with function 10 and off with 11
    """
    uobj = _login(mock_post)
    mock_post.side_effect = None
    mock_post.return_value = _response(b'{"result": 0}')

    assert uobj.set_area_chime(area=1, state=True) is True
    data = mock_post.call_args[1]['data']
    assert mock_post.call_args[0][0] == 'http://zerowire/user/keyfunction.cgi'
    assert data['fnum'] == 10
    assert data['start'] == 0
    assert data['mask'] == 1

    assert uobj.set_area_chime(area=1, state=False) is True
    assert mock_post.call_args[1]['data']['fnum'] == 11

    # An unknown area sends nothing
    mock_post.reset_mock()
    assert uobj.set_area_chime(area=9, state=True) is False
    assert mock_post.call_count == 0


@mock.patch('requests.Session.post')
def test_xgen8_area_siren_state(mock_post):
    """
    The siren state of an area comes from byte 17 of its status; byte 19
    alone (a keypad beep) does not count
    """
    uobj = _login(mock_post)
    assert uobj.areas[0]['states']['siren'] is False

    uobj = _login(mock_post, '00' * 17 + '01' + '00' * 22)
    assert uobj.areas[0]['states']['siren'] is True

    uobj = _login(mock_post, '00' * 19 + '01' + '00' * 20)
    assert uobj.areas[0]['states']['siren'] is False


@mock.patch('requests.Session.post')
def test_xgen8_siren_command(mock_post):
    """
    The siren is started with function 13 and silenced with function 4
    """
    uobj = _login(mock_post)
    mock_post.side_effect = None
    mock_post.return_value = _response(b'{"result": 0}')

    assert uobj.set_siren(area=1, state=True) is True
    data = mock_post.call_args[1]['data']
    assert mock_post.call_args[0][0] == 'http://zerowire/user/keyfunction.cgi'
    assert data['fnum'] == 13
    assert data['start'] == 0
    assert data['mask'] == 1

    assert uobj.set_siren(area=1, state=False) is True
    assert mock_post.call_args[1]['data']['fnum'] == 4

    # An unknown area sends nothing
    mock_post.reset_mock()
    assert uobj.set_siren(area=9, state=True) is False
    assert mock_post.call_count == 0

    # A panel that does not answer is reported as a failure
    mock_post.return_value = _response(b'')
    assert uobj.set_siren(area=1, state=True) is False


@mock.patch('requests.Session.post')
def test_xgen8_events(mock_post):
    """
    Panel events are read and parsed
    """
    uobj = _login(mock_post)
    mock_post.side_effect = None
    mock_post.return_value = _response(json.dumps(EVENTS).encode('utf-8'))

    events = uobj.events(count=2)

    data = mock_post.call_args[1]['data']
    assert mock_post.call_args[0][0] == \
        'http://zerowire/user/api/events.cgi'
    assert data['count'] == 2
    assert data['start'] == 65535
    assert data['type'] == 0

    assert len(events) == 2

    bypass = events[0]
    assert bypass['id'] == 732
    assert bypass['code'] == '570'
    assert bypass['restore'] is False
    assert bypass['description'] == 'Bypass'
    assert bypass['zone'] == 12
    assert bypass['area'] == 1
    assert bypass['user'] == 4
    assert bypass['user_name'] == 'HA'
    assert bypass['lines'] == ['Bypass*', 'MC BEDROOM', 'HOME', 'HA']
    assert bypass['time'] == datetime(
        2026, 11, 7, 15, 26, 45, tzinfo=timezone.utc)

    arm = events[1]
    assert arm['code'] == '401'
    assert arm['restore'] is True
    assert arm['description'] == 'Turn On Away'
    assert arm['zone'] is None

    # A panel that can not be read returns None
    mock_post.return_value = _response(b'')
    assert uobj.events() is None


def test_parse_event_tolerates_missing_fields():
    """
    An event without text, time or ids still parses
    """
    event = UltraSync.parse_event({'id': 1})
    assert event['id'] == 1
    assert event['time'] is None
    assert event['description'] == ''
    assert event['lines'] == []
    assert event['user_name'] is None


def test_parse_event_user_name_needs_a_user():
    """
    The last text line is only taken as a user name when the event has a user
    """
    text = 'Burglary%20%20MC%20KITCHEN%20%20HOME'
    for user in (None, 0):
        event = UltraSync.parse_event(
            {'id': 2, 'user': user, 'sensor': 8, 'text': text})
        assert event['user_name'] is None
