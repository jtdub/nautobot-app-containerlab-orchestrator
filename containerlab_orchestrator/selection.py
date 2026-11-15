"""Select the devices of a lab from its device filter, its Dynamic Group, and its device list."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import EMPTY_VALUES
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from nautobot.apps.utils import get_filterset_for_model, is_single_choice_field
from nautobot.dcim.models import Device

from containerlab_orchestrator.validators import validate_json_object

UNKNOWN_FILTER_MESSAGE = "Unknown filter field."
"""The error for a filter key that the Device filter set does not have."""

VALUE_SHAPE_MESSAGE = "Use a value or a list of values."
"""The error for a filter value that is not a scalar or a flat list of scalars."""

LIST_FOR_SINGLE_VALUE_MESSAGE = "Use one value, not a list."
"""The error for a list value of a filter that accepts one value only."""

EMPTY_VALUE_MESSAGE = "Enter a value."
"""The error for a filter value that cleans to an empty value, so that the filter does nothing."""

NOT_DEVICE_GROUP_MESSAGE = "Select a Dynamic Group of devices."
"""The error for a Dynamic Group of other objects."""

MATCH_ALL_GROUP_MESSAGE = "This group selects all devices."
"""The error for a Dynamic Group with no filter and no child group."""

EMPTY_SELECTION_MESSAGE = "The device filter, the Dynamic Group, and the devices of lab {lab} select no device."
"""The error for a lab whose sources select no device."""

HIDDEN_DEVICES_MESSAGE = "You cannot view {count} of the devices that lab {lab} selects."
"""The error for a lab that selects devices that the user cannot view."""

SCALAR_TYPES = (str, int, float, bool, type(None))
"""The JSON types that a filter value or a filter list item can have."""


def _value_shape_message(filterset, name, value):
    """Return the error for a filter key or a filter value of the wrong shape, else an empty string."""
    if name not in filterset.filters:
        return UNKNOWN_FILTER_MESSAGE
    is_list = isinstance(value, list)
    if not isinstance(value, SCALAR_TYPES) and not (is_list and all(isinstance(item, SCALAR_TYPES) for item in value)):
        return VALUE_SHAPE_MESSAGE
    if is_list and is_single_choice_field(filterset, name):
        return LIST_FOR_SINGLE_VALUE_MESSAGE
    return ""


def _is_empty(value):
    """Return True if a cleaned filter value selects nothing to filter by."""
    if isinstance(value, QuerySet):
        return not value.exists()
    return value in EMPTY_VALUES


def validate_device_filter(device_filter):
    """Return the bound Device filter set of a device filter.

    The check does not read `STRICT_FILTERING`. Each error item is `"<filter>: <message>"`, sorted by filter name.

    Raises:
        ValidationError: On `device_filter`, if a key or a value is not valid.
    """
    filterset_class = get_filterset_for_model(Device)
    empty_filterset = filterset_class()
    problems = []
    good_data = {}
    for name, value in device_filter.items():
        message = _value_shape_message(empty_filterset, name, value)
        if message:
            problems.append((name, message))
        else:
            good_data[name] = value
    filterset = filterset_class(good_data, Device.objects.all())
    if not filterset.is_valid():
        for name, messages in filterset.errors.items():
            problems.extend(("" if name == "__all__" else name, message) for message in messages)
    problems.extend(
        (name, EMPTY_VALUE_MESSAGE)
        for name in good_data
        if name not in filterset.errors and _is_empty(filterset.form.cleaned_data.get(name))
    )
    if problems:
        problems.sort(key=lambda problem: problem[0])
        raise ValidationError(
            {"device_filter": [f"{name}: {message}" if name else message for name, message in problems]}
        )
    return filterset


def _check_dynamic_group(group):
    """Reject a Dynamic Group of other objects, a group with a bad filter, and a group that selects all devices.

    Raises:
        ValidationError: On `dynamic_group`.
    """
    if group.content_type_id != ContentType.objects.get_for_model(Device).pk:
        raise ValidationError({"dynamic_group": [NOT_DEVICE_GROUP_MESSAGE]})
    messages = []
    for member_group in [group, *group.get_descendants()]:
        if member_group.group_type == "dynamic-filter" and member_group.filter:
            try:
                validate_device_filter(member_group.filter)
            except ValidationError as error:
                messages.extend(f"{member_group}: {message}" for message in error.message_dict["device_filter"])
        elif member_group.group_type == "dynamic-filter" or (
            member_group.group_type == "dynamic-set" and not member_group.children.exists()
        ):
            messages.append(f"{member_group}: {MATCH_ALL_GROUP_MESSAGE}")
    if messages:
        raise ValidationError({"dynamic_group": messages})


def _refreshed_members(group):
    """Return the members of a Dynamic Group after a refresh of its member cache.

    If another refresh of the group runs at the same time, return the cached members.
    """
    try:
        with transaction.atomic():
            return group.update_cached_members()
    except IntegrityError:
        return group.members


def select_devices(lab, user=None):
    """Return the devices that a saved lab selects, limited to the devices that the user can view.

    The function writes the member cache of the Dynamic Group. With no user, it applies no view limit.

    Raises:
        ValidationError: If a source is not valid, if the lab selects no device, or if the user cannot view
            some devices.
        RuntimeError: If the Dynamic Group has a group type that Nautobot does not know.
    """
    try:
        validate_json_object(lab.device_filter)
    except ValidationError as error:
        raise ValidationError({"device_filter": error.messages}) from error
    sources = Q(pk__in=lab.devices.values("pk"))
    if lab.device_filter:
        sources |= Q(pk__in=validate_device_filter(lab.device_filter).qs.values("pk"))
    if lab.dynamic_group is not None:
        _check_dynamic_group(lab.dynamic_group)
        sources |= Q(pk__in=_refreshed_members(lab.dynamic_group).values("pk"))
    selected = Device.objects.filter(sources)
    devices = selected if user is None else Device.objects.restrict(user, "view").filter(sources)
    if not devices.exists():
        raise ValidationError(EMPTY_SELECTION_MESSAGE.format(lab=lab))
    hidden_count = selected.count() - devices.count() if user is not None else 0
    if hidden_count:
        raise ValidationError(HIDDEN_DEVICES_MESSAGE.format(count=hidden_count, lab=lab))
    return devices
