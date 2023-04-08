"""Parser logic for ETS XML files."""
from __future__ import annotations

import logging

from xknxproject.__version__ import __version__
from xknxproject.loader import (
    ApplicationProgramLoader,
    HardwareLoader,
    ManufacturerLoader,
    ProjectLoader,
)
from xknxproject.models import (
    MEDIUM_TYPES,
    Area,
    CommunicationObject,
    Device,
    DeviceInstance,
    Flags,
    GroupAddress,
    HardwareToPrograms,
    KNXProject,
    Line,
    Product,
    Space,
    XMLArea,
    XMLGroupAddress,
    XMLSpace,
)
from xknxproject.zip.extractor import KNXProjContents

_LOGGER = logging.getLogger("xknxproject.log")


class XMLParser:
    """Class that parses XMLs and returns useful information."""

    def __init__(self, knx_proj_contents: KNXProjContents) -> None:
        """Initialize the parser."""
        self.knx_proj_contents = knx_proj_contents
        self.spaces: list[XMLSpace] = []
        self.group_addresses: list[XMLGroupAddress] = []
        self.areas: list[XMLArea] = []
        self.devices: list[DeviceInstance] = []

    def parse(self) -> KNXProject:
        """Parse ETS files."""
        self.load()

        communication_objects: dict[str, CommunicationObject] = {}
        devices_dict: dict[str, Device] = {}
        for device in self.devices:
            device_com_objects: list[str] = []
            for com_object in device.com_object_instance_refs:
                if com_object.links:
                    com_object_key = f"{device.individual_address}/{com_object.ref_id}"
                    communication_objects[com_object_key] = CommunicationObject(
                        name=com_object.name or com_object.text,
                        device_address=device.individual_address,
                        dpt_type=com_object.datapoint_type,  # type: ignore[typeddict-item]
                        flags=Flags(
                            read=com_object.read_flag,  # type: ignore[typeddict-item]
                            write=com_object.write_flag,  # type: ignore[typeddict-item]
                            communication=com_object.communication_flag,  # type: ignore[typeddict-item]
                            update=com_object.update_flag,  # type: ignore[typeddict-item]
                            read_on_init=com_object.read_on_init_flag,  # type: ignore[typeddict-item]
                            transmit=com_object.transmit_flag,  # type: ignore[typeddict-item]
                        ),
                        group_address_links=com_object.links,
                    )
                    device_com_objects.append(com_object_key)

            devices_dict[device.individual_address] = Device(
                name=device.name or device.product_name,
                product_name=device.product_name,
                description=device.hardware_name,
                individual_address=device.individual_address,
                manufacturer_name=device.manufacturer_name,
                communication_object_ids=device_com_objects,
            )

        topology_dict: dict[str, Area] = {}
        for area in self.areas:
            lines_dict: dict[str, Line] = {}
            for line in area.lines:
                devices_topology: list[str] = []
                for device in line.devices:
                    devices_topology.append(device.individual_address)
                lines_dict[str(line.address)] = Line(
                    name=line.name,
                    description=line.description,
                    devices=devices_topology,
                    medium_type=MEDIUM_TYPES.get(line.medium_type, "Unknown"),
                )
            topology_dict[str(area.address)] = Area(
                name=area.name, description=area.description, lines=lines_dict
            )

        group_address_dict: dict[str, GroupAddress] = {}
        for group_address in self.group_addresses:
            _com_object_ids = [
                com_object_id
                for com_object_id, com_object in communication_objects.items()
                if group_address.identifier in com_object["group_address_links"]
            ]
            group_address_dict[group_address.identifier] = GroupAddress(
                name=group_address.name,
                identifier=group_address.identifier,
                raw_address=group_address.raw_address,
                address=group_address.address,
                dpt_type=group_address.dpt_type,
                communication_object_ids=_com_object_ids,
                description=group_address.description,
            )

        space_dict: dict[str, Space] = {}
        for space in self.spaces:
            space_dict[space.name] = self.recursive_convert_spaces(space)

        return KNXProject(
            version=__version__,
            communication_objects=communication_objects,
            topology=topology_dict,
            devices=devices_dict,
            group_addresses=group_address_dict,
            locations=space_dict,
        )

    def recursive_convert_spaces(self, space: XMLSpace) -> Space:
        """Convert spaces to the final output format."""
        subspaces: dict[str, Space] = {}
        for subspace in space.spaces:
            subspaces[subspace.name] = self.recursive_convert_spaces(subspace)

        return Space(type=space.type.value, devices=space.devices, spaces=subspaces)

    def load(self) -> None:
        """Load XML files."""
        (
            self.group_addresses,
            self.areas,
            self.devices,
            self.spaces,
        ) = ProjectLoader.load(self.knx_proj_contents)

        ManufacturerLoader.load(
            self.knx_proj_contents.root_path / "knx_master.xml", self.devices
        )

        products_dict: dict[str, Product] = {}
        hardware_application_map: HardwareToPrograms = {}
        for _products, _hardware_programs in [
            HardwareLoader.load(hardware_file)
            for hardware_file in HardwareLoader.get_hardware_files(
                self.knx_proj_contents
            )
        ]:
            products_dict.update(_products)
            hardware_application_map.update(_hardware_programs)

        for device in self.devices:
            try:
                product = products_dict[device.product_ref]
            except KeyError:
                _LOGGER.warning(
                    "Could not find hardware product for device %s with product_ref %s",
                    device.individual_address,
                    device.product_ref,
                )
                continue
            device.product_name = product.text
            device.hardware_name = product.hardware_name

            try:
                application_program_ref = hardware_application_map[
                    device.hardware_program_ref
                ]
            except KeyError:
                _LOGGER.warning(
                    "Could not find application_program_ref for device %s with hardware_program_ref %s",
                    device.individual_address,
                    device.hardware_program_ref,
                )
                continue
            device.application_program_ref = application_program_ref
            for com_object in device.com_object_instance_refs:
                com_object.resolve_com_object_ref_id(application_program_ref)

        application_programs = (
            ApplicationProgramLoader.get_application_program_files_for_devices(
                self.knx_proj_contents.root_path, self.devices
            )
        )
        for application_program_file, devices in application_programs.items():
            ApplicationProgramLoader.load(application_program_file, devices)
