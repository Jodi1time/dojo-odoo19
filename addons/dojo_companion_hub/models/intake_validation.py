"""Bounded intake values. This module deliberately has no ORM or provider I/O."""
import re
import unicodedata
from datetime import date


class IntakeValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("INTAKE_FIELDS_REQUIRED")


def normalized_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def validate_intake(payload, today):
    issues = []

    def problem(field, message):
        issues.append({"field": field, "message": message})

    def shape(value, keys, field):
        if not isinstance(value, dict) or set(value) != set(keys.split()):
            problem(field, "Use the current intake form; its fields do not match this server.")
            return False
        return True

    def string(value, field, label, maximum=200, required=False):
        if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
            problem(field, "%s is invalid or too long." % label)
            return ""
        value = value.strip()
        if required and not value:
            problem(field, "Enter %s." % label.lower())
        return value

    def contact(value, prefix):
        name = string(value.get("name"), prefix + ".name", "Full name", required=True)
        email = string(value.get("email"), prefix + ".email", "Email")
        phone = string(value.get("phone"), prefix + ".phone", "Phone", maximum=40)
        if email and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
            problem(prefix + ".email", "Enter a valid email address.")
        if phone and (not re.fullmatch(r"[+0-9(). \-]+", phone) or not 7 <= len(re.sub(r"\D", "", phone)) <= 15):
            problem(prefix + ".phone", "Enter a phone number with 7 to 15 digits.")
        return {"name": name, "email": email, "phone": phone}

    if not shape(payload, "studentType student guardian reviewRef", "intake"):
        raise IntakeValidationError(issues)
    kind = payload["studentType"]
    if kind not in ("adult", "child"):
        problem("studentType", "Choose Adult or Child (under 18).")
    student = payload["student"]
    if not shape(student, "name email phone dateOfBirth gender", "student"):
        raise IntakeValidationError(issues)
    clean = contact(student, "student")
    birth = student.get("dateOfBirth")
    try:
        if not isinstance(birth, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birth):
            raise ValueError()
        dob = date.fromisoformat(birth)
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if dob > today or age > 120:
            raise ValueError()
        if kind == "child" and age >= 18 or kind == "adult" and age < 18:
            problem("studentType", "The student type does not match the birth date. Use Child for under 18.")
    except (ValueError, TypeError):
        problem("student.dateOfBirth", "Enter a real birth date, not in the future (maximum age 120).")
    clean["dateOfBirth"] = birth
    gender = student.get("gender")
    if gender not in ("", "male", "female", "other"):
        problem("student.gender", "Choose a listed gender or leave it unrecorded.")
    clean["gender"] = gender
    guardian = None
    if kind == "adult":
        if payload["guardian"] is not None:
            problem("guardian", "A standalone adult intake must not create or link a guardian.")
        if not clean["email"] and not clean["phone"]:
            problem("student.email", "Enter at least one contact method: email or phone.")
    elif kind == "child":
        value = payload["guardian"]
        if not isinstance(value, dict) or value.get("kind") not in ("new", "existing"):
            problem("guardian", "Choose a verified existing guardian or enter a new guardian.")
        elif value["kind"] == "new":
            if shape(value, "kind name email phone", "guardian"):
                guardian = {"kind": "new", **contact(value, "guardian")}
                if not guardian["email"] and not guardian["phone"]:
                    problem("guardian.email", "Enter the guardian’s email or phone.")
        elif shape(value, "kind partnerId householdId expectedVersion expectedHouseholdVersion", "guardian"):
            for key in ("partnerId", "householdId"):
                identifier = value[key]
                if not isinstance(identifier, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", identifier) or int(identifier) > 2147483647:
                    problem("guardian." + key, "Select an existing guardian and household from this school.")
            for key in ("expectedVersion", "expectedHouseholdVersion"):
                if type(value[key]) is not int or value[key] < 0:
                    problem("guardian." + key, "Refresh the guardian selection before continuing.")
            guardian = dict(value)
    reference = string(payload["reviewRef"], "reviewRef", "Identity and guardian review reference", maximum=300, required=True)
    if issues:
        raise IntakeValidationError(issues)
    return {"studentType": kind, "student": clean, "guardian": guardian, "reviewRef": reference}
