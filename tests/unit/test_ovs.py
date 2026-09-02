# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import pytest

from openstack_hypervisor.ovs import (
    OVSCli,
    OVSCommandError,
)


class TestOVSCli:
    def test_list_bridge_interfaces_filters_types(self):
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:

            def fake_vsctl(*args, retry=True):
                if args[0] == "list-ifaces":
                    return "eth0\npatch-port\ninternal-port\n"
                if args[0] == "--bare" and "find" in args:
                    return "eth0\n"
                return ""

            mock_vsctl.side_effect = fake_vsctl

            ifaces = ovs.list_bridge_interfaces("br-ex")
            assert ifaces == ["eth0"]

            mock_vsctl.assert_any_call("list-ifaces", "br-ex")
            mock_vsctl.assert_any_call(
                "--bare",
                "--columns=name",
                "find",
                "Interface",
                "type!=patch",
                "type!=internal",
            )

    def test_list_table(self):
        ovs = OVSCli()
        mock_data = """
{"data":[[["map",[["dpdk-init","try"],["dpdk-socket-mem","4096"]]]]],"headings":["other_config"]}
"""
        with patch.object(ovs, "vsctl", return_value=mock_data):
            out = ovs.list_table("mock-table", "mock-record", ["mock-column"])

            exp_out = {
                "other_config": {
                    "dpdk-init": "try",
                    "dpdk-socket-mem": "4096",
                }
            }
            assert exp_out == out

    def test_set(self):
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:
            ovs.set(
                "mock-table",
                "mock-record",
                "mock-column",
                {"key1": "val1", "key2": "val2"},
            )

            mock_vsctl.assert_called_once_with(
                "set",
                "mock-table",
                "mock-record",
                "mock-column:key1=val1",
                "mock-column:key2=val2",
            )

    def test_set_check(self):
        ovs = OVSCli()
        mock_current_settings = {
            "dpdk-init": "try",
            "dpdk-socket-mem": "4096",
        }
        mock_updates = {
            "hw-offload": "true",
        }
        mock_applied_settings = dict(mock_current_settings)
        mock_applied_settings.update(mock_updates)

        with (
            patch.object(ovs, "list_table") as mock_list_table,
            patch.object(ovs, "set"),
        ):
            mock_list_table.side_effect = [
                {"other_config": mock_current_settings},
                {"other_config": mock_applied_settings},
            ]

            config_changed = ovs.set_check(
                "mock-table", "mock-record", "other_config", mock_updates
            )
            assert config_changed

            config_changed = ovs.set_check(
                "mock-table", "mock-record", "other_config", mock_updates
            )
            assert not config_changed

    def test_add_bridge(self):
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:
            ovs.add_bridge("bridge-name", "datapath-name", "fake-arg")

            mock_vsctl.assert_called_once_with(
                "--may-exist",
                "add-br",
                "bridge-name",
                "--",
                "set",
                "bridge",
                "bridge-name",
                "datapath_type=datapath-name",
                "fake-arg",
            )

    def test_del_port(self):
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:
            ovs.del_port("bridge-name", "port-name")

            mock_vsctl.assert_called_once_with(
                "--if-exists", "del-port", "bridge-name", "port-name"
            )

    def test_add_port(self):
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:
            ovs.add_port(
                "bridge-name",
                "port-name",
                port_type="dpdk",
                options={"dpdk-devargs": "pci-address"},
                mtu=9000,
            )

            mock_vsctl.assert_called_once_with(
                "--may-exist",
                "add-port",
                "bridge-name",
                "port-name",
                "--",
                "set",
                "Interface",
                "port-name",
                "type=dpdk",
                "--",
                "set",
                "Interface",
                "port-name",
                "mtu-request=9000",
                "--",
                "set",
                "Interface",
                "port-name",
                "options:dpdk-devargs=pci-address",
            )

    def test_appctl_success(self):
        """Test appctl executes successfully and returns output."""
        ovs = OVSCli("unix:/some/db.sock", switchd_ctl_socket="unix:/some/ctl.sock")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "dpctl/show output"
            mock_run.return_value.returncode = 0

            result = ovs.appctl("dpctl/show")

            assert result == "dpctl/show output"
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == [
                "ovs-appctl",
                "--target",
                "unix:/some/ctl.sock",
                "dpctl/show",
            ]

    def test_appctl_no_socket_raises_error(self):
        """Test appctl raises OVSCommandError when switchd_ctl_socket is not set."""
        ovs = OVSCli("unix:/some/db.sock")
        with pytest.raises(OVSCommandError, match="switchd_ctl_socket is not configured"):
            ovs.appctl("dpctl/show")

    def test_appctl_binary_not_found(self):
        """Test appctl raises OVSCommandError when binary not found."""
        ovs = OVSCli("unix:/some/db.sock", switchd_ctl_socket="unix:/some/ctl.sock")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            with pytest.raises(OVSCommandError, match="ovs-appctl binary not found"):
                ovs.appctl("dpctl/show")

    def test_appctl_command_error(self):
        """Test appctl raises OVSCommandError on command failure."""
        import subprocess

        ovs = OVSCli("unix:/some/db.sock", switchd_ctl_socket="unix:/some/ctl.sock")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr="error message")

            with pytest.raises(OVSCommandError, match="error message"):
                ovs.appctl("dpctl/show")

    def test_get_dpdk_initialized_true(self):
        """Test get_dpdk_initialized returns True when DPDK is initialized."""
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:
            mock_vsctl.return_value = '"true"\n'

            result = ovs.get_dpdk_initialized()

            assert result is True
            mock_vsctl.assert_called_once_with("get", "Open_vSwitch", ".", "dpdk_initialized")

    def test_get_dpdk_initialized_false(self):
        """Test get_dpdk_initialized returns False when DPDK is not initialized."""
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:
            mock_vsctl.return_value = '"false"\n'

            result = ovs.get_dpdk_initialized()

            assert result is False

    def test_get_dpdk_initialized_on_error(self):
        """Test get_dpdk_initialized returns False on command error."""
        ovs = OVSCli()
        with patch.object(ovs, "vsctl") as mock_vsctl:
            mock_vsctl.side_effect = OVSCommandError("error")

            result = ovs.get_dpdk_initialized()

            assert result is False
