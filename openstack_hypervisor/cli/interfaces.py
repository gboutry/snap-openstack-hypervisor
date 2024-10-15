# Copyright 2024 Canonical Ltd.
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
import glob
import json
import logging
import pathlib
from typing import Iterable

import click
import pyroute2
from pyroute2.ndb.objects.interface import Interface

VALUE_FORMAT = "value"
JSON_FORMAT = "json"

logger = logging.getLogger(__name__)


def get_interfaces(ndb) -> list[Interface]:
    """Get all interfaces from the system."""
    return list(ndb.interfaces.values())


def is_link_local(address: str) -> bool:
    """Check if address is link local."""
    return address.startswith("fe80")


def is_interface_configured(nic: Interface) -> bool:
    """Check if interface has an IP address configured."""
    ipaddr = nic.ipaddr
    if ipaddr is None:
        return False
    for record in ipaddr.summary():
        if (ip := record["address"]) and not is_link_local(ip):
            logger.debug("Interface %r has IP address %r", nic["ifname"], ip)
            return True
    return False


def load_virtual_interfaces() -> list[str]:
    """Load virtual interfaces from the system."""
    virtual_nic_dir = "/sys/devices/virtual/net/*"
    return [pathlib.Path(p).name for p in glob.iglob(virtual_nic_dir)]


def filter_candidate_nics(nics: Iterable[Interface]) -> list[str]:
    """Return a list of candidate nics.

    Candidate nics are:
      - not part of a bond
      - not a virtual nic except for bond and vlan
      - not configured (unless include_configured is True)
    """
    configured_nics = []
    virtual_nics = load_virtual_interfaces()
    for nic in nics:
        ifname = nic["ifname"]
        logger.debug("Checking interface %r", ifname)

        if nic["slave_kind"] == "bond":
            logger.debug("Ignoring interface %r, it is part of a bond", ifname)
            continue

        if ifname in virtual_nics:
            kind = nic["kind"]
            if kind in ("bond", "vlan"):
                logger.debug("Interface %r is a %s", ifname, kind)
            else:
                logger.debug(
                    "Ignoring interface %r, it is a virtual interface, kind: %s", ifname, kind
                )
                continue

        is_configured = is_interface_configured(nic)
        logger.debug("Interface %r is configured: %r", ifname, is_configured)
        if not is_configured:
            logger.debug("Adding interface %r as a candidate", ifname)
            configured_nics.append(ifname)

    return configured_nics


def display_nics(nics: list[str], candidate_nics: list[str], format: str):
    """Display the result depending on the format."""
    if format == VALUE_FORMAT:
        print("All nics:")
        for nic in nics:
            print(nic)
        if candidate_nics:
            print("Candidate nics:")
            for nic in candidate_nics:
                print(nic)
    elif format == JSON_FORMAT:
        print(json.dumps({"nics": nics, "candidates": candidate_nics}, indent=2))


@click.command("list-nics")
@click.option(
    "-f",
    "--format",
    default=JSON_FORMAT,
    type=click.Choice([VALUE_FORMAT, JSON_FORMAT]),
    help="Output format",
)
def list_nics(format: str):
    """List nics that are candidates for use by OVN/OVS subsystem.

    This nic will be used by OVS to provide external connectivity to the VMs.
    """
    with pyroute2.NDB() as ndb:
        nics = get_interfaces(ndb)
        candidate_nics = filter_candidate_nics(nics)
        display_nics([nic["ifname"] for nic in nics], candidate_nics, format)
