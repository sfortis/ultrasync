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
from unittest import mock
import requests
from os.path import join
from os.path import dirname

from ultrasync import UltraSync
from ultrasync.common import AlarmScene
from ultrasync.common import NX595EVendor
from ultrasync.common import ZoneStatus

# Disable logging for a cleaner testing output
import logging
logging.disable(logging.CRITICAL)

# The xGen8 NXG-8-Z-BO capture is reused, with zone conditions added to it
ULTRASYNC_TEST_VAR_DIR = \
    join(dirname(__file__), 'var', NX595EVendor.XGEN8, 'nxg8zbo')

# 23 zone banks, as an xGen8 panel sends them.  Bank 0 (Not Ready) is the
# one from the capture.  Zone 1 has a low battery (bank 6), zone 2 a tamper
# (bank 1) and zone 4 a mains failure (bank 19, past the first 18 banks).
ZONE_STATUS = [
    'D405', '0200', '0000', '0000', '0000', '0000', '0100', '0000',
    '0000', '0100', '0000', '0040', '0000', '0000', '0000', '0000',
    '0100', '0000', '0000', '0800', '0000', '0000', '0000',
]

ZONE_SEQUENCE = [159] + [1] * (len(ZONE_STATUS) - 1)


def _response(content):
    """Return a mocked HTTP response carrying the content given."""
    obj = mock.Mock()
    obj.content = content
    obj.status_code = requests.codes.ok
    return obj


def _fixture(name):
    """Return the content of a capture file."""
    with open(join(ULTRASYNC_TEST_VAR_DIR, name), 'rb') as f:
        return f.read()


def _zones_page():
    """Return the zone page with the zone conditions above set."""
    page = _fixture('zones.htm').decode('utf-8')
    page = re.sub(
        r'var zoneStatus\s*=\s*\[[^\]]*\];',
        'var zoneStatus = {};'.format(json.dumps(ZONE_STATUS)), page)
    page = re.sub(
        r'var zoneSequence\s*=\s*new Array\([^)]*\);',
        'var zoneSequence = new Array({});'.format(
            ','.join(str(s) for s in ZONE_SEQUENCE)), page)
    return page.encode('utf-8')


@mock.patch('requests.Session.post')
def test_xgen8_zone_flags_and_system_faults(mock_post):
    """
    Zone conditions and panel system faults are reported
    """
    mock_post.side_effect = (
        _response(_fixture('area.htm')), _response(_zones_page()))

    uobj = UltraSync()
    assert uobj.login()
    assert uobj.vendor is NX595EVendor.XGEN8

    # The zone status is still only Ready or Not Ready
    assert uobj.zones[0]['status'] == ZoneStatus.READY
    assert uobj.zones[0]['flags'] == [ZoneStatus.LOW_BATTERY]

    assert uobj.zones[1]['status'] == ZoneStatus.READY
    assert uobj.zones[1]['flags'] == [ZoneStatus.TAMPER]

    assert uobj.zones[2]['status'] == ZoneStatus.NOT_READY
    assert uobj.zones[2]['flags'] == []

    # A condition past the first 18 banks is read as well
    assert uobj.zones[3]['flags'] == [ZoneStatus.MAINS_FAILURE]

    # Banks that are not a condition (chime, bypass and so on) are left out
    assert uobj.zones[8]['flags'] == []

    # No fault was reported yet
    assert uobj.system_faults == []

    # The low battery clears on zone bank 6, and the panel sends a system
    # fault with that zone update
    seq = {
        'time': '60D1B269',
        'area': list(uobj._asequence),
        'zone': list(ZONE_SEQUENCE),
    }
    seq['zone'][6] += 1
    zstate = {
        'time': '60D1B26C',
        'zbank': 6,
        'zseq': seq['zone'][6],
        'bankstates': '0000',
        'system': ['Low%20Battery', 'AC%20Fail'],
    }

    mock_post.reset_mock()
    mock_post.side_effect = (
        _response(json.dumps(seq).encode('utf-8')),
        _response(json.dumps(zstate).encode('utf-8')))

    sequence = uobj.zones[0]['sequence']
    details = uobj.details(max_age_sec=0)

    assert mock_post.call_count == 2
    assert mock_post.call_args_list[1][0][0] == \
        'http://zerowire/user/zstate.json'

    assert uobj.zones[0]['flags'] == []
    # A change in the flags counts as a zone change
    assert uobj.zones[0]['sequence'] != sequence

    assert details['system_faults'] == ['Low Battery', 'AC Fail']
    assert uobj.system_faults == ['Low Battery', 'AC Fail']

    # The area status still comes from the last area update only
    assert uobj._UltraSync__extra_area_status == []


def test_system_faults_placeholder_is_ignored():
    """
    The "No System Faults" text some panels send is not a fault
    """
    uobj = UltraSync()
    uobj._UltraSync__system_faults = ['No System Faults']
    assert uobj.system_faults == []

    uobj._UltraSync__system_faults = ['', 'Low Battery']
    assert uobj.system_faults == ['Low Battery']


@mock.patch('requests.Session.post')
def test_xgen8_unsupported_alarm_scenes(mock_post):
    """
    Fire, medical and panic are refused on xGen8 panels instead of being
    sent as a disarm
    """
    mock_post.side_effect = (
        _response(_fixture('area.htm')), _response(_zones_page()))

    uobj = UltraSync()
    assert uobj.login()

    for scene in (AlarmScene.PANIC, AlarmScene.FIRE, AlarmScene.MEDICAL):
        mock_post.reset_mock()
        assert uobj.set_alarm(state=scene) is False
        # Nothing was sent to the panel
        assert mock_post.call_count == 0
