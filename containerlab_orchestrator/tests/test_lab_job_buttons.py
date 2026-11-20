"""Tests for the lab job buttons of the lab page."""

import re
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.template.loader import render_to_string
from django.test import override_settings
from django.urls import reverse
from django.utils.html import escapejs
from nautobot.apps.choices import JobResultStatusChoices
from nautobot.apps.testing import TestCase
from nautobot.extras.models import Job, JobResult, Status

from containerlab_orchestrator import views
from containerlab_orchestrator.choices import LabStatusChoices
from containerlab_orchestrator.jobs import DestroyLab, RedeployLab
from containerlab_orchestrator.models import Lab
from containerlab_orchestrator.tests.fixtures import create_lab_job_result, grant_permission
from containerlab_orchestrator.tests.test_views import LAB_DETAIL_PERMISSIONS, create_lab_view_data

read_lab_job_access = views._read_lab_job_access  # pylint: disable=protected-access
LAB_JOB_PERMISSIONS = (*LAB_DETAIL_PERMISSIONS, "extras.run_job", "containerlab_orchestrator.change_lab")
LAB_JOB_MODULE = "containerlab_orchestrator.jobs"
ALL_LAB_STATUSES = tuple(LabStatusChoices.values())
ALL_LABELS = ["Build", "Deploy", "Redeploy", "Destroy", "Inspect"]
BUSY_TITLE = "A lab job of this lab runs."
EXPECTED_LAB_JOB_BUTTONS = {
    "Build": ("BuildLabTopology", "default", ALL_LAB_STATUSES, ""),
    "Deploy": (
        "DeployLab",
        "default",
        (LabStatusChoices.BUILT, LabStatusChoices.DESTROYED, LabStatusChoices.FAILED),
        "Lab lab-a is {status}. Only a Built, Destroyed, or Failed lab can deploy.",
    ),
    "Redeploy": (
        "RedeployLab",
        "danger",
        (LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED),
        "Lab lab-a is {status}. Only a Deployed or Failed lab can redeploy.",
    ),
    "Destroy": (
        "DestroyLab",
        "danger",
        (LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED),
        "Lab lab-a is {status}. Only a Deployed or Failed lab can destroy.",
    ),
    "Inspect": (
        "InspectLab",
        "default",
        (LabStatusChoices.BUILT, LabStatusChoices.DEPLOYED, LabStatusChoices.FAILED, LabStatusChoices.DESTROYED),
        "Lab lab-a is {status}. The job inspects only a Built, Deployed, Failed, or Destroyed lab.",
    ),
}
"""For each button label: the job class, the button color, the statuses that enable it, and the disabled tooltip."""


def lab_job_button(label):
    """Return the lab job button of the label."""
    return next(button for button in views.LAB_JOB_BUTTONS if button.label == label)


def run_form_url(job_class_name, lab):
    """Return the run form link of the lab job, with the lab selected."""
    url = reverse("extras:job_run_by_class_path", kwargs={"class_path": f"{LAB_JOB_MODULE}.{job_class_name}"})
    return f"{url}?lab={lab.pk}"


def button_html(body, button):
    """Return the HTML element of the button in the page body, or None when the page has no such element."""
    match = re.search(rf'<(a|button)\b[^>]*\bid="component-{button.component_id}".*?</\1>', body, flags=re.DOTALL)
    return match.group(0) if match else None


def button_ids(body):
    """Return the component ids of the lab job buttons in the page order."""
    lab_job_ids = {button.component_id for button in views.LAB_JOB_BUTTONS}
    return [found for found in re.findall(r'id="component-([0-9a-f]+)"', body) if found in lab_job_ids]


@override_settings(EXEMPT_VIEW_PERMISSIONS=[])
class LabJobButtonSetupTestCase(TestCase):
    """Open the lab page as the owner of lab-a, with the five lab jobs turned on and lab-a Failed."""

    @classmethod
    def setUpTestData(cls):
        _, _, cls.labs = create_lab_view_data()
        cls.lab_a, cls.lab_b, _ = cls.labs
        cls.statuses = {status.name: status for status in Status.objects.get_for_model(Lab)}

    def setUp(self):
        super().setUp()
        self.add_permissions(*LAB_JOB_PERMISSIONS)
        Lab.objects.filter(pk=self.lab_a.pk).update(owner=self.user)
        Job.objects.filter(module_name=LAB_JOB_MODULE).update(enabled=True)
        self.set_lab_a_status(LabStatusChoices.FAILED)

    def set_lab_a_status(self, name):
        """Set the status of lab-a to the status of the name."""
        Lab.objects.filter(pk=self.lab_a.pk).update(status=self.statuses[name])

    def make_superuser(self):
        """Make the test user a superuser."""
        self.user.is_superuser = True
        self.user.save()

    def get_body(self, lab=None, query=None):
        """Return the HTML of the lab page, lab-a by default, after a check of the status code."""
        response = self.client.get((lab or self.lab_a).get_absolute_url(), query)
        self.assertHttpStatus(response, 200)
        return response.content.decode()

    def shown_labels(self, body):
        """Return the labels of the lab job buttons in the page body, in the order of LAB_JOB_BUTTONS."""
        return [button.label for button in views.LAB_JOB_BUTTONS if button_html(body, button) is not None]

    def assert_disabled(self, element, title):
        """Assert that the element is a disabled button with the title and no link."""
        self.assertTrue(element.startswith('<button type="button"'))
        self.assertIn(f'disabled="disabled" aria-disabled="true" title="{title}"', element)
        self.assertNotIn("href", element)


class LabJobButtonTestCase(LabJobButtonSetupTestCase):  # pylint: disable=too-many-public-methods
    """Test the Build, Deploy, Redeploy, Destroy, and Inspect buttons of the lab page."""

    def test_owner_sees_five_buttons_that_link_to_the_run_form_with_the_lab(self):
        body = self.get_body()

        for label, (job_class_name, color, _, _) in EXPECTED_LAB_JOB_BUTTONS.items():
            with self.subTest(label=label):
                element = button_html(body, lab_job_button(label))
                self.assertTrue(element.startswith(f'<a href="{run_form_url(job_class_name, self.lab_a)}"'))
                self.assertIn(f'class="btn btn-{color}"', element)
                self.assertIn(label, element)
                self.assertNotIn("disabled", element)
        self.assertEqual(button_ids(body), [lab_job_button(label).component_id for label in ALL_LABELS])

    def test_run_form_of_each_button_selects_the_lab(self):
        for job_class_name, job_name in (
            ("BuildLabTopology", "Build Lab Topology"),
            ("DeployLab", "Deploy Lab"),
            ("RedeployLab", "Redeploy Lab"),
            ("DestroyLab", "Destroy Lab"),
            ("InspectLab", "Inspect Lab"),
        ):
            with self.subTest(job=job_class_name):
                response = self.client.get(run_form_url(job_class_name, self.lab_a))

                self.assertHttpStatus(response, 200)
                self.assertRegex(
                    response.content.decode(), rf'<option value="{self.lab_a.pk}"\s+selected\s*>lab-a</option>'
                )
                self.assertContains(response, f"<title>{job_name} - Nautobot</title>")

    def test_turned_off_job_has_no_button(self):
        Job.objects.filter(module_name=LAB_JOB_MODULE, job_class_name="DeployLab").update(enabled=False)

        self.assertEqual(self.shown_labels(self.get_body()), ["Build", "Redeploy", "Destroy", "Inspect"])

    def test_job_of_another_module_with_a_lab_job_name_gives_no_button(self):
        lab_deploy = Job.objects.filter(module_name=LAB_JOB_MODULE, job_class_name="DeployLab")
        lab_deploy.update(enabled=False)
        other_deploy = lab_deploy.values().get()
        other_deploy.update(id=None, module_name="other_app.jobs", name="Other Deploy Lab", enabled=True)
        Job.objects.create(**other_deploy)

        self.assertEqual(self.shown_labels(self.get_body()), ["Build", "Redeploy", "Destroy", "Inspect"])

    def test_run_permission_limited_to_one_job_shows_only_its_button(self):
        self.remove_permissions("extras.run_job")
        grant_permission(self.user, Job, "run", {"job_class_name": "InspectLab"})

        self.assertEqual(self.shown_labels(self.get_body()), ["Inspect"])

    def test_user_without_run_permission_sees_no_button(self):
        self.remove_permissions("extras.run_job")

        self.assertEqual(self.shown_labels(self.get_body()), [])

    def test_not_installed_job_has_no_button(self):
        Job.objects.filter(module_name=LAB_JOB_MODULE, job_class_name="DestroyLab").update(installed=False)

        self.assertEqual(self.shown_labels(self.get_body()), ["Build", "Deploy", "Redeploy", "Inspect"])

    def test_non_owner_sees_only_inspect(self):
        body = self.get_body(self.lab_b)

        self.assertEqual(self.shown_labels(body), ["Inspect"])
        inspect_link = run_form_url("InspectLab", self.lab_b)
        self.assertTrue(button_html(body, lab_job_button("Inspect")).startswith(f'<a href="{inspect_link}"'))

    def test_superuser_sees_changing_buttons_on_lab_of_other_owner(self):
        self.make_superuser()

        self.assertEqual(self.shown_labels(self.get_body(self.lab_b)), ALL_LABELS)

    def test_lab_with_no_owner_shows_changing_buttons_only_to_superuser(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(owner=None)

        self.assertEqual(self.shown_labels(self.get_body()), ["Inspect"])
        self.make_superuser()
        self.assertEqual(self.shown_labels(self.get_body()), ALL_LABELS)

    def test_user_without_change_permission_sees_no_button(self):
        self.remove_permissions("containerlab_orchestrator.change_lab")

        self.assertEqual(self.shown_labels(self.get_body()), [])

    def test_buttons_follow_the_status_rules(self):
        for status in ALL_LAB_STATUSES:
            self.set_lab_a_status(status)
            body = self.get_body()
            for label, (job_class_name, _, enabled_statuses, message) in EXPECTED_LAB_JOB_BUTTONS.items():
                with self.subTest(status=status, label=label):
                    element = button_html(body, lab_job_button(label))
                    if status in enabled_statuses:
                        self.assertTrue(element.startswith(f'<a href="{run_form_url(job_class_name, self.lab_a)}"'))
                        self.assertNotIn("disabled", element)
                    else:
                        self.assert_disabled(element, message.format(status=status))

    def test_added_status_enables_only_build(self):
        paused = Status.objects.create(name="Paused")
        paused.content_types.add(ContentType.objects.get_for_model(Lab))
        Lab.objects.filter(pk=self.lab_a.pk).update(status=paused)

        body = self.get_body()

        self.assertTrue(button_html(body, lab_job_button("Build")).startswith('<a href="'))
        for label in ("Deploy", "Redeploy", "Destroy", "Inspect"):
            with self.subTest(label=label):
                message = EXPECTED_LAB_JOB_BUTTONS[label][3]
                self.assert_disabled(button_html(body, lab_job_button(label)), message.format(status="Paused"))

    def test_buttons_show_only_on_the_main_tab(self):
        Lab.objects.filter(pk=self.lab_a.pk).update(topology_yaml="name: lab-a\n")
        body = self.get_body(query={"tab": "topology_yaml"})
        yaml_tab = body[body.index('<div id="topology_yaml"') :]
        main_tab = body[body.index('<div id="main"') : body.index('<div id="topology_yaml"')]

        self.assertIn("Download YAML", yaml_tab)
        self.assertEqual(self.shown_labels(yaml_tab), [])
        self.assertEqual(self.shown_labels(main_tab), ALL_LABELS)
        self.add_permissions("extras.view_objectchange")
        response = self.client.get(
            reverse("plugins:containerlab_orchestrator:lab_changelog", kwargs={"pk": self.lab_a.pk})
        )
        self.assertHttpStatus(response, 200)
        self.assertEqual(self.shown_labels(response.content.decode()), [])

    def test_access_is_read_once(self):
        with mock.patch.object(views, "_read_lab_job_access", wraps=read_lab_job_access) as read_access:
            body = self.get_body()

        self.assertEqual(len(self.shown_labels(body)), 5)
        read_access.assert_called_once()

    def test_access_read_query_counts(self):
        lab_a = Lab.objects.get(pk=self.lab_a.pk)
        self.make_superuser()
        with self.assertNumQueries(4):
            access = read_lab_job_access(self.user, lab_a)
        self.assertEqual(access, views.LabJobAccess(frozenset(views.LAB_JOB_CLASS_NAMES), True, True, False))

        self.user.is_superuser = False
        self.user.save()
        Job.objects.filter(module_name=LAB_JOB_MODULE).update(enabled=False)
        user = type(self.user).objects.get(pk=self.user.pk)
        self.assertTrue(user.has_perm("extras.run_job"))
        with self.assertNumQueries(1):
            self.assertEqual(read_lab_job_access(user, lab_a), views.NO_ACCESS)

        self.remove_permissions("extras.run_job")
        user = type(self.user).objects.get(pk=self.user.pk)
        self.assertFalse(user.has_perm("extras.run_job"))
        with self.assertNumQueries(0):
            self.assertEqual(read_lab_job_access(user, lab_a), views.NO_ACCESS)

    def test_access_read_error_shows_no_button(self):
        with (
            mock.patch.object(views, "_read_lab_job_access", side_effect=DatabaseError("connection lost")),
            self.assertLogs("containerlab_orchestrator.views", level="ERROR") as logs,
        ):
            body = self.get_body()

        self.assertEqual(self.shown_labels(body), [])
        self.assertIn("<strong>Nodes</strong>", body)
        self.assertIn("lab-a", logs.output[0])
        self.assertIn("connection lost", logs.output[0])

    def test_destroy_and_redeploy_have_the_confirmation_script(self):
        body = self.get_body()
        scripts = re.findall(r"<script>(.*?)</script>", body, flags=re.DOTALL)

        for label, description in (
            ("Redeploy", RedeployLab.Meta.description),
            ("Destroy", DestroyLab.Meta.description),
        ):
            with self.subTest(label=label):
                element_id = f'document.getElementById("component-{lab_job_button(label).component_id}")'
                matching = [script for script in scripts if element_id in script]
                self.assertEqual(len(matching), 1)
                self.assertIn(f'window.confirm("{escapejs(description)}")', matching[0])
                self.assertIn('addEventListener("click"', matching[0])
                self.assertIn("event.preventDefault()", matching[0])
        for label in ("Build", "Deploy", "Inspect"):
            with self.subTest(label=label):
                component_id = lab_job_button(label).component_id
                self.assertFalse([script for script in scripts if component_id in script])
        for button in views.LAB_JOB_BUTTONS:
            with self.subTest(button=button.label):
                self.assertNotIn("onclick", button_html(body, button))

    def test_confirmation_script_escapes_the_text_and_listens_only_on_a_link(self):
        script = render_to_string(
            views.LAB_JOB_CONFIRM_SCRIPT,
            {"component": lab_job_button("Destroy"), "confirm_text": 'Say "yes" </script>'},
        )

        self.assertIn(r'window.confirm("Say \u0022yes\u0022 \u003C/script\u003E")', script)
        self.assertIn("if (button instanceof HTMLAnchorElement) {", script)


class LabJobButtonBusyTestCase(LabJobButtonSetupTestCase):
    """Test that a lab job of the lab that runs disables the five lab job buttons."""

    def assert_titles(self, body, titles):
        """Assert that each lab job button is disabled with the title of its label, or enabled for no title."""
        for label in ALL_LABELS:
            with self.subTest(label=label):
                element = button_html(body, lab_job_button(label))
                if titles.get(label):
                    self.assert_disabled(element, titles[label])
                else:
                    self.assertTrue(element.startswith('<a href="'))

    def test_running_lab_job_disables_the_five_buttons(self):
        create_lab_job_result({"lab": str(self.lab_a.pk)})

        self.assert_titles(self.get_body(), dict.fromkeys(ALL_LABELS, BUSY_TITLE))

    def test_results_that_do_not_disable_the_buttons(self):
        other_job = Job.objects.exclude(module_name=LAB_JOB_MODULE).first()
        self.assertIsNotNone(other_job)
        changes_of_a_busy_result = {
            "older than 6 hours": {"age": timedelta(hours=6, minutes=1)},
            "status SUCCESS": {"status": JobResultStatusChoices.STATUS_SUCCESS},
            "another lab": {"task_kwargs": {"lab": str(uuid4())}},
            "a job of another module": {"job_model": other_job},
            "no lab input": {"task_kwargs": {}},
        }
        for name, change in changes_of_a_busy_result.items():
            with self.subTest(name):
                JobResult.objects.all().delete()
                create_lab_job_result(**{"task_kwargs": {"lab": str(self.lab_a.pk)}, **change})
                self.assert_titles(self.get_body(), {})

    def test_stale_result_disables_until_six_hours(self):
        result = create_lab_job_result({"lab": str(self.lab_a.pk)}, age=timedelta(hours=5, minutes=59))
        self.assert_titles(self.get_body(), dict.fromkeys(ALL_LABELS, BUSY_TITLE))

        result.delete()
        create_lab_job_result({"lab": str(self.lab_a.pk)}, age=timedelta(hours=6, minutes=1))
        self.assert_titles(self.get_body(), {})

    def test_status_reason_comes_before_busy_reason(self):
        self.set_lab_a_status(LabStatusChoices.PLANNED)
        create_lab_job_result({"lab": str(self.lab_a.pk)})

        titles = {label: values[3].format(status="Planned") for label, values in EXPECTED_LAB_JOB_BUTTONS.items()}
        self.assert_titles(self.get_body(), {**titles, "Build": BUSY_TITLE})

    def test_no_busy_query_without_a_lab_job_button(self):
        create_lab_job_result({"lab": str(self.lab_a.pk)})
        for name, permission in (
            ("no run permission", "extras.run_job"),
            ("no change permission", "containerlab_orchestrator.change_lab"),
        ):
            with self.subTest(name), mock.patch.object(views, "is_busy") as busy:
                self.remove_permissions(permission)
                self.assertEqual(self.shown_labels(self.get_body()), [])
                busy.assert_not_called()
                self.add_permissions(permission)

        self.make_superuser()
        lab_a = Lab.objects.get(pk=self.lab_a.pk)
        with self.assertNumQueries(4):
            self.assertTrue(read_lab_job_access(self.user, lab_a).busy)
