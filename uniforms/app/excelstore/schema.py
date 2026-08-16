"""Which sheets and columns we expect, and every header alias we tolerate.

Adding an alias here is the cheapest possible fix when the client's spreadsheet
turns out to call something by another name, so this list is meant to grow.
"""
from __future__ import annotations

from .parsing import Column

EMPLOYEES_SHEET = "Employees"
ITEMS_SHEET = "UniformItems"
ENTITLEMENTS_SHEET = "Entitlements"
ISSUANCES_SHEET = "Issuances"

#: Sheet name aliases, so we find the right tab whatever they called it.
SHEET_ALIASES: dict[str, tuple[str, ...]] = {
    EMPLOYEES_SHEET: ("employees", "employee", "staff", "staff list", "people", "roster", "master"),
    ITEMS_SHEET: ("uniformitems", "items", "uniforms", "uniform", "catalogue", "catalog", "clothing"),
    ENTITLEMENTS_SHEET: ("entitlements", "entitlement", "allocation", "allocations", "rules", "kit"),
    ISSUANCES_SHEET: ("issuances", "issuance", "issued", "issues", "log", "records", "distribution",
                      "issue log", "issuelog", "issue record", "issue records", "uniform log",
                      "issued items", "handover", "handovers"),
}

EMPLOYEE_COLUMNS = (
    Column("employee_number", ("employee number", "employee no", "emp no", "empno", "staff id",
                               "staff no", "employee id", "payroll number", "payroll no", "id",
                               "number"), required=True),
    Column("full_name", ("full name", "name", "employee name", "staff name", "names")),
    Column("email", ("email", "email address", "e-mail", "work email")),
    Column("manager_email", ("manager email", "supervisor email", "line manager email", "manager")),
    Column("department", ("department", "dept", "division", "unit", "section")),
    Column("role", ("role", "job title", "position", "designation", "grade", "job role")),
    Column("join_date", ("join date", "joining date", "date joined", "start date", "hire date",
                         "date of joining", "doj", "employment date")),
    Column("status", ("status", "employment status", "active", "state")),
    Column("shirt_size", ("shirt size", "shirt", "top size")),
    Column("trouser_size", ("trouser size", "trouser", "trousers", "waist", "trouser waist", "pant size")),
    Column("blazer_size", ("blazer size", "blazer", "jacket size", "coat size")),
    Column("shoe_size", ("shoe size", "shoes", "shoe", "footwear size")),
)

ITEM_COLUMNS = (
    Column("item_code", ("item code", "code", "item", "item id", "sku"), required=True),
    Column("name", ("name", "item name", "description", "uniform item")),
    Column("category", ("category", "type", "group")),
    Column("renewal_cycle_months", ("renewal cycle months", "cycle months", "cycle", "renewal months",
                                    "renewal cycle", "months", "validity months", "lifespan months")),
    Column("default_quantity", ("default quantity", "quantity", "qty", "default qty", "issue quantity")),
    Column("size_key", ("size key", "size type", "sizing")),
    Column("active", ("active", "in use", "enabled", "status")),
)

ENTITLEMENT_COLUMNS = (
    Column("role", ("role", "job title", "position", "designation", "grade"), required=True),
    Column("item_code", ("item code", "code", "item", "sku"), required=True),
    Column("quantity", ("quantity", "qty", "number", "count")),
)

ISSUANCE_COLUMNS = (
    Column("employee_number", ("employee number", "employee no", "emp no", "staff id", "staff no",
                               "employee id", "payroll number", "id", "number"), required=True),
    Column("item_code", ("item code", "code", "item", "sku", "uniform item"), required=True),
    Column("issued_date", ("issued date", "date issued", "issue date", "date", "given on",
                           "collection date"), required=True),
    Column("quantity", ("quantity", "qty", "number", "count")),
    Column("size", ("size", "issued size")),
    Column("issued_by", ("issued by", "given by", "storekeeper", "handled by", "officer")),
    Column("cycle_months", ("cycle months", "renewal cycle months", "cycle")),
    Column("notes", ("notes", "note", "remarks", "comment", "comments")),
)

#: Column order used when this app creates a workbook from scratch, and when it
#: appends a row to an existing sheet that lacks one of these headers.
ISSUANCE_WRITE_ORDER = (
    "employee_number", "item_code", "issued_date", "quantity", "size",
    "issued_by", "cycle_months", "notes",
)

ISSUANCE_HEADERS = (
    "Employee Number", "Item Code", "Issued Date", "Quantity", "Size",
    "Issued By", "Cycle Months", "Notes",
)

EMPLOYEE_HEADERS = (
    "Employee Number", "Full Name", "Email", "Manager Email", "Department", "Role",
    "Join Date", "Status", "Shirt Size", "Trouser Size", "Blazer Size", "Shoe Size",
)

ITEM_HEADERS = (
    "Item Code", "Name", "Category", "Renewal Cycle Months", "Default Quantity",
    "Size Key", "Active",
)

ENTITLEMENT_HEADERS = ("Role", "Item Code", "Quantity")

#: Maps an employee size column onto the ``size_key`` an item declares.
SIZE_FIELDS = {
    "shirt_size": "shirt",
    "trouser_size": "trouser",
    "blazer_size": "blazer",
    "shoe_size": "shoe",
}
