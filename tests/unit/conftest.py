# Copyright 2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
from unittest.mock import MagicMock, patch

import pytest
from snaphelpers import Snap, SnapConfig, SnapServices

libvirt_mock = MagicMock()
libvirt_mock.VIR_DOMAIN_RUNNING = 1
libvirt_mock.VIR_DOMAIN_SHUTDOWN = 5
sys.modules["libvirt"] = libvirt_mock


@pytest.fixture
def snap_env():
    """Environment variables defined in the snap.

    This is primarily used to setup the snaphelpers bit.
    """
    yield {
        "SNAP": "/snap/mysnap/2",
        "SNAP_COMMON": "/var/snap/mysnap/common",
        "SNAP_DATA": "/var/snap/mysnap/2",
        "SNAP_INSTANCE_NAME": "",
        "SNAP_NAME": "mysnap",
        "SNAP_REVISION": "2",
        "SNAP_USER_COMMON": "",
        "SNAP_USER_DATA": "",
        "SNAP_VERSION": "1.2.3",
        "SNAP_REAL_HOME": "/home/ubuntu",
    }


@pytest.fixture
def snap(snap_env):
    snap = Snap(environ=snap_env)
    snap.config = MagicMock(SnapConfig)
    snap.services = MagicMock(SnapServices)
    yield snap


@pytest.fixture
def os_makedirs():
    with patch("os.makedirs") as p:
        yield p


@pytest.fixture
def check_call():
    with patch("subprocess.check_call") as p:
        yield p


@pytest.fixture
def check_output():
    with patch("subprocess.check_output") as p:
        yield p


@pytest.fixture
def link_lookup():
    with patch("pyroute2.IPRoute.link_lookup") as p:
        yield p


@pytest.fixture
def split():
    yield "1.2.3.4/24"


@pytest.fixture
def addr():
    with patch("pyroute2.IPRoute.addr") as p:
        yield p


@pytest.fixture
def link():
    with patch("pyroute2.IPRoute.link") as p:
        yield p


@pytest.fixture
def ip_interface():
    with patch("ipaddress.ip_interface") as p:
        yield p


@pytest.fixture
def sleep():
    with patch("time.sleep") as p:
        yield p


@pytest.fixture
def libvirt():
    yield libvirt_mock


def mock_vm(name, xml, active):
    vm = MagicMock()
    vm.name = name
    vm.isActive.return_value = active

    def shutdown():
        vm.isActive.return_value = False

    vm.destroy.side_effect = shutdown
    vm.XMLDesc.return_value = xml
    return vm


@pytest.fixture
def vms():
    with open("tests/unit/virsh_openstack.xml", "r") as f:
        os_xml = f.read()
    with open("tests/unit/virsh_non_openstack.xml", "r") as f:
        non_os_xml = f.read()

    vms = {
        "vm1": mock_vm("vm1", os_xml, True),
        "vm2": mock_vm("vm2", non_os_xml, True),
        "vm3": mock_vm("vm3", os_xml, False),
    }
    yield vms
