"""Fast checks for the exact validator used by native Odoo intake."""
import importlib.util
from datetime import date
from pathlib import Path
import unittest

source = Path(__file__).resolve().parents[2] / 'addons/dojo_companion_hub/models/intake_validation.py'
spec = importlib.util.spec_from_file_location('intake_validation', source)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class IntakeValidationTests(unittest.TestCase):
    def payload(self):
        return {'studentType': 'adult', 'student': {'name': ' Test Student ', 'dateOfBirth': '2000-01-01', 'gender': '',
            'email': 'test@example.invalid', 'phone': ''}, 'guardian': None, 'reviewRef': 'checked-reference'}

    def test_normalizes_without_mutating_caller(self):
        value = self.payload()
        result = validator.validate_intake(value, date(2026, 10, 13))
        self.assertEqual(result['student']['name'], 'Test Student')
        self.assertEqual(value['student']['name'], ' Test Student ')

    def test_missing_fields_are_collected_together(self):
        value = self.payload()
        value['student'].update(name='', email='', dateOfBirth='')
        value['reviewRef'] = ''
        with self.assertRaises(validator.IntakeValidationError) as caught:
            validator.validate_intake(value, date(2026, 10, 13))
        self.assertGreaterEqual(len(caught.exception.issues), 4)

    def test_real_calendar_dates_and_adult_boundary(self):
        for birth in ('2025-02-29', '2026-10-14', '2010-01-01', '2000-01-01junk', None):
            with self.subTest(birth=birth):
                value = self.payload()
                value['student']['dateOfBirth'] = birth
                with self.assertRaises(validator.IntakeValidationError):
                    validator.validate_intake(value, date(2026, 10, 13))
        value = self.payload()
        value['student']['dateOfBirth'] = '2008-10-13'
        validator.validate_intake(value, date(2026, 10, 13))

    def test_extra_authority_fields_rejected(self):
        value = self.payload()
        value['companyId'] = '1'
        with self.assertRaises(validator.IntakeValidationError):
            validator.validate_intake(value, date(2026, 10, 13))

    def test_child_requires_guardian_not_just_student_phone(self):
        value = self.payload()
        value['studentType'] = 'child'
        value['student']['dateOfBirth'] = '2018-01-01'
        with self.assertRaises(validator.IntakeValidationError):
            validator.validate_intake(value, date(2026, 10, 13))
        value['guardian'] = {'kind': 'new', 'name': 'Parent', 'email': '', 'phone': '+1 (555) 123-4567'}
        validator.validate_intake(value, date(2026, 10, 13))

    def test_contacts_and_control_characters_rejected(self):
        for field, value in [('email', 'not-an-email'), ('phone', '12'), ('phone', '+1;DROP'), ('name', 'Hi\nThere')]:
            p = self.payload()
            p['student'][field] = value
            with self.assertRaises(validator.IntakeValidationError):
                validator.validate_intake(p, date(2026, 10, 13))

    def test_bool_is_not_a_guardian_record_version(self):
        value = self.payload()
        value['studentType'] = 'child'
        value['student']['dateOfBirth'] = '2018-01-01'
        value['guardian'] = {'kind': 'existing', 'partnerId': '1', 'householdId': '2', 'expectedVersion': True, 'expectedHouseholdVersion': 1}
        with self.assertRaises(validator.IntakeValidationError):
            validator.validate_intake(value, date(2026, 10, 13))

    def test_normalized_name_collapses_case_and_whitespace(self):
        self.assertEqual(validator.normalized_name('  TEST   Student '), 'test student')
