# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import contextlib
import json
import logging
import subprocess
import uuid
from collections.abc import Generator


class OVSError(RuntimeError):
    """Common base class for OVS-related errors."""


class OVSCommandError(OVSError):
    """Raised when querying OVS state fails."""


class OVSTimeoutError(OVSError, TimeoutError):
    """Raised when an OVS command times out."""


def _parse_ovsdb_data(data):
    """Parse OVSDB data according to RFC 7047.

    https://tools.ietf.org/html/rfc7047#section-5.1
    """
    if isinstance(data, list) and len(data) == 2:
        if data[0] == "set":
            return [_parse_ovsdb_data(element) for element in data[1]]
        if data[0] == "map":
            return {_parse_ovsdb_data(key): _parse_ovsdb_data(value) for key, value in data[1]}
        if data[0] == "uuid":

            return uuid.UUID(data[1])
    return data


class OVSCli:
    """Client for interacting with Open vSwitch via ovs-vsctl."""

    def __init__(
        self,
        db_sock: str | None = None,
        switchd_ctl_socket: str | None = None,
        timeout: int | None = None,
    ):
        """Initialize OVS CLI client.

        Args:
            db_sock: Optional database socket path to use for all commands.
            switchd_ctl_socket: Optional vswitchd control socket path for appctl commands.
            timeout: Optional default timeout in seconds for commands.
        """
        self.db_sock = db_sock
        self.switchd_ctl_socket = switchd_ctl_socket
        self._timeout = timeout

    @contextlib.contextmanager
    def with_timeout(self, timeout: int) -> Generator["OVSCli", None, None]:
        """Context manager to temporarily set a command timeout.

        Args:
            timeout: Timeout in seconds to set for commands within the context.

        Yields:
            self: The OVSCli instance with updated timeout.
        """
        original_timeout = self._timeout
        self._timeout = timeout
        try:
            yield self
        finally:
            self._timeout = original_timeout

    def _execute_vsctl(
        self, args: list[str], retry: bool = True, timeout: int | None = None
    ) -> str:
        """Execute ovs-vsctl with the provided arguments.

        This is the internal method that performs the actual subprocess execution.

        Args:
            args: Arguments to pass to ovs-vsctl.
            retry: Whether to use the --retry flag.
            timeout: Optional timeout in seconds for the command.

        Returns:
            The stdout output from the command.

        Raises:
            OVSCommandError: If the command fails or ovs-vsctl is not found.
        """
        cmd = ["ovs-vsctl"]
        if self.db_sock:
            cmd.append("--db=" + self.db_sock)
        if retry:
            cmd.append("--retry")
        timeout = timeout or self._timeout
        if timeout is not None:
            cmd.append(f"--timeout={timeout}")
        cmd.extend(args)
        logging.debug("Executing command: %s", " ".join(cmd))

        try:
            completed = subprocess.run(  # nosec B603
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise OVSCommandError("ovs-vsctl binary not found") from exc
        except subprocess.CalledProcessError as exc:  # pragma: no cover - defensive
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or f"Command failed with exit code {exc.returncode}"
            if "Alarm clock" in details:
                raise OVSTimeoutError(details) from exc
            raise OVSCommandError(details) from exc

        return completed.stdout

    def vsctl(
        self,
        *args: str,
        retry: bool = True,
        timeout: int | None = None,
    ) -> str:
        """Run ovs-vsctl with the provided arguments and return stdout.

        Args:
            *args: Arguments to pass to ovs-vsctl.
            retry: Whether to use the --retry flag.
            timeout: Optional timeout in seconds for the command.

        Returns:
            The stdout output from the command.

        Raises:
            OVSCommandError: If the command fails or ovs-vsctl is not found.
        """
        return self._execute_vsctl(list(args), retry=retry, timeout=timeout)

    def list_bridges(self) -> list[str]:
        """Return the list of bridges currently present in OVS.

        Returns:
            Sorted list of bridge names.
        """
        output = self.vsctl("list-br")
        return sorted({bridge for bridge in output.splitlines() if bridge.strip()})

    def list_bridge_interfaces(self, bridge: str) -> list[str]:
        """Return interfaces attached to a bridge.

        Args:
            bridge: Name of the bridge to query.

        Returns:
            Sorted list of interface names attached to the bridge.
        """
        output = self.vsctl("list-ifaces", bridge)
        bridge_ifaces = {iface.strip() for iface in output.splitlines() if iface.strip()}

        if not bridge_ifaces:
            return []

        # Filter out patch and internal ports
        actual_ifaces_output = self.vsctl(
            "--bare",
            "--columns=name",
            "find",
            "Interface",
            "type!=patch",
            "type!=internal",
        )
        actual_ifaces = {
            iface.strip() for iface in actual_ifaces_output.splitlines() if iface.strip()
        }

        return sorted(bridge_ifaces & actual_ifaces)

    def set(self, table: str, record: str, column: str, settings: dict[str, str]) -> None:
        """Set column values in an OVS table.

        Args:
            table: OVS table name (e.g., 'open', 'Open_vSwitch', 'Port').
            record: Record to modify.
            column: Column to modify (e.g., 'external_ids', 'other_config').
            settings: Dictionary of key=value pairs to set.

        Raises:
            OVSCommandError: If the command fails.
        """
        if not settings:
            logging.warning("No ovs values to set, skipping...")
            return

        args = ["set", table, record]
        for key, value in settings.items():
            args.append(f"{column}:{key}={value}")
        self.vsctl(*args)

    def list_table(self, table: str, record: str, columns: list[str] | None = None) -> dict:
        """List table entries and parse JSON output.

        Args:
            table: OVS table name.
            record: Record to query.
            columns: Optional list of column names to retrieve.

        Returns:
            Dictionary of parsed table data.

        Raises:
            OVSCommandError: If the command fails.
        """
        args = ["--format", "json", "--if-exists"]
        if columns:
            args.append(f"--columns={','.join(columns)}")
        args.extend(["list", table, record])

        try:
            output = self.vsctl(*args)
        except OVSCommandError:
            # The columns may not exist. --if-exists only applies to the record, not columns.
            return {}

        raw_json = json.loads(output)
        headings = raw_json["headings"]
        data = raw_json["data"]

        parsed = {}
        # We've requested a single record.
        for record_data in data:
            for position, heading in enumerate(headings):
                parsed[heading] = _parse_ovsdb_data(record_data[position])

        return parsed

    def add_bridge(self, bridge_name: str, datapath_type: str = "system", *cmd_args: str) -> None:
        """Add a bridge to OVS.

        Args:
            bridge_name: Name of the bridge to add.
            datapath_type: Datapath type ("system" or "netdev").
            *cmd_args: Additional arguments to pass (e.g., "protocols=OpenFlow13").

        Raises:
            OVSCommandError: If the command fails.
        """
        args = [
            "--may-exist",
            "add-br",
            bridge_name,
            "--",
            "set",
            "bridge",
            bridge_name,
            f"datapath_type={datapath_type}",
        ]
        args.extend(cmd_args)
        self.vsctl(*args)

    def add_port(
        self,
        bridge_name: str,
        port_name: str,
        port_type: str | None = None,
        options: dict[str, str] | None = None,
        mtu: int | None = None,
    ) -> None:
        """Add a port to a bridge.

        Args:
            bridge_name: Name of the bridge.
            port_name: Name of the port to add.
            port_type: Optional port type (e.g., "dpdk", "patch").
            options: Optional port options dictionary.
            mtu: Optional MTU value.

        Raises:
            OVSCommandError: If the command fails.
        """
        args = ["--may-exist", "add-port", bridge_name, port_name]

        set_interface = ["--", "set", "Interface", port_name]
        if port_type:
            args.extend(set_interface + [f"type={port_type}"])
        if mtu:
            args.extend(set_interface + [f"mtu-request={mtu}"])
        if options:
            args.extend(set_interface)
            for key, value in options.items():
                args.append(f"options:{key}={value}")
        self.vsctl(*args)

    def del_port(self, bridge_name: str, port_name: str) -> None:
        """Delete a port from a bridge.

        Args:
            bridge_name: Name of the bridge.
            port_name: Name of the port to delete.

        Raises:
            OVSCommandError: If the command fails.
        """
        self.vsctl("--if-exists", "del-port", bridge_name, port_name)

    def add_bond(
        self,
        bridge_name: str,
        bond_name: str,
        ports: list[str],
        bond_mode: str | None = None,
        lacp_mode: str | None = None,
        lacp_time: str | None = None,
    ) -> None:
        """Add a bond to a bridge.

        Args:
            bridge_name: Name of the bridge.
            bond_name: Name of the bond to create.
            ports: List of port names to include in the bond.
            bond_mode: Bond mode (e.g., "balance-tcp", "active-backup").
            lacp_mode: LACP mode ("active", "passive", or "off").
            lacp_time: LACP time ("fast" or "slow").

        Raises:
            OVSCommandError: If the command fails.
        """
        args = ["--may-exist", "add-bond", bridge_name, bond_name]
        args.extend(ports)

        # Build arguments for port settings after bond creation
        if bond_mode or lacp_mode or lacp_time:
            args.extend(["--", "set", "port", bond_name])

            if bond_mode:
                args.append(f"bond_mode={bond_mode}")

            if lacp_mode:
                args.append(f"lacp={lacp_mode}")

            if lacp_time:
                args.append(f"other-config:lacp-time={lacp_time}")

        self.vsctl(*args)

    def set_check(self, table: str, record: str, column: str, settings: dict[str, str]) -> bool:
        """Apply settings and return whether changes were made.

        Args:
            table: OVS table name.
            record: Record to modify.
            column: Column to modify.
            settings: Dictionary of settings to apply.

        Returns:
            True if changes were made, False otherwise.

        Raises:
            OVSCommandError: If the command fails.
        """
        config_changed = False
        current_values = self.list_table(table, record, [column]).get(column, {})
        for key, new_val in settings.items():
            if key not in current_values or str(new_val) != str(current_values[key]):
                config_changed = True

        if config_changed:
            self.set(table, record, column, settings)

        return config_changed

    def appctl(self, *args: str) -> str:
        """Run ovs-appctl with the provided arguments and return stdout.

        ovs-appctl is used to query and control OVS daemons at runtime.
        Common use cases include querying DPDK status and hardware offload statistics.

        Args:
            *args: Arguments to pass to ovs-appctl (e.g., "dpctl/show", "dpif/show").

        Returns:
            The stdout output from the command.

        Raises:
            OVSCommandError: If the command fails, ovs-appctl is not found,
                            or if switchd_ctl_socket is not configured.

        Example:
            >>> ovs_cli.appctl("dpctl/show")  # Query datapath
            >>> ovs_cli.appctl("dpctl/offload-stats-show")  # Query offload stats
        """
        if not self.switchd_ctl_socket:
            raise OVSCommandError(
                "switchd_ctl_socket is not configured. Cannot run appctl command. "
                "Ensure external OVS is properly connected."
            )

        cmd = ["ovs-appctl", "--target", self.switchd_ctl_socket]
        cmd.extend(args)
        logging.debug("Executing command: %s", " ".join(cmd))

        try:
            completed = subprocess.run(  # nosec B603
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise OVSCommandError("ovs-appctl binary not found") from exc
        except subprocess.CalledProcessError as exc:  # pragma: no cover - defensive
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or f"Command failed with exit code {exc.returncode}"
            raise OVSCommandError(details) from exc

        return completed.stdout

    def get_dpdk_initialized(self) -> bool:
        """Check if DPDK is initialized in OVS.

        Queries the Open_vSwitch table to determine if DPDK has been
        successfully initialized.

        Returns:
            True if DPDK is initialized (dpdk_initialized="true"), False otherwise.

        Raises:
            OVSCommandError: If the command fails.
        """
        try:
            result = self.vsctl("get", "Open_vSwitch", ".", "dpdk_initialized")
            return result.strip().strip('"').lower() == "true"
        except OVSCommandError:
            return False
