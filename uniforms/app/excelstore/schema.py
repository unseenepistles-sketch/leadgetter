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
ORDERS_SHEET = "Orders"
OVERRIDES_SHEET = "RenewalOverrides"
CHANGELOG_SHEET = "ChangeLog"

#: Sheet name aliases, so we find the right tab whatever they called it.
SHEET_ALIASES: dict[str, tuple[str, ...]] = {
    EMPLOYEES_SHEET: ("employees", "employee", "staff", "staff list", "people", "roster", "master"),
    ITEMS_SHEET: ("uniformitems", "items", "uniforms", "uniform", "catalogue", "catalog", "clothing"),
    ENTITLEMENTS_SHEET: ("entitlements", "entitlement", "allocation", "allocations", "rules", "kit"),
    ISSUANCES_SHEET: ("issuances", "issuance", "issued", "issues", "log", "records", "distribution",
                      "issue log", "issuelog", "issue record", "issue records", "uniform log",
                      "issued items", "handover", "handovers"),
    ORDERS_SHEET: ("orders", "order", "orders log", "orderlog", "order log", "ordered",
                   "purchase", "purchases", "requisitions", "requisition"),
    OVERRIDES_SHEET: ("renewaloverrides", "overrides", "override", "renewal overrides",
                      "manual renewals", "exceptions"),
    CHANGELOG_SHEET: ("changelog", "change log", "audit", "audit log", "edits", "history"),
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
    Column("order_id", ("order id", "order", "order ref", "order reference", "orderid")),
)

ORDER_COLUMNS = (
    Column("order_id", ("order id", "orderid", "order", "id", "reference", "ref"), required=True),
    Column("employee_number", ("employee number", "employee no", "staff id", "staff no",
                               "emp no", "employee id", "payroll number", "id"), required=True),
    Column("item_code", ("item code", "code", "item", "sku", "uniform item"), required=True),
    Column("ordered_date", ("ordered date", "order date", "date ordered", "ordered", "date"),
           required=True),
    Column("quantity", ("quantity", "qty", "number", "count", "ordered qty")),
    Column("supplier_ref", ("supplier ref", "supplier reference", "supplier", "po", "po number")),
    Column("notes", ("notes", "note", "remarks", "comment", "comments")),
    Column("cancelled", ("cancelled", "canceled", "void", "voided")),
)

OVERRIDE_COLUMNS = (
    Column("employee_number", ("employee number", "employee no", "staff id", "id",
                               "employee id"), required=True),
    Column("item_code", ("item code", "code", "item", "sku"), required=True),
    Column("next_due", ("next due", "next due date", "override date", "renewal due",
                        "override next due"), required=True),
    Column("reason", ("reason", "justification", "why", "remarks", "notes")),
    Column("authorised_by", ("authorised by", "authorized by", "approved by", "authoriser",
                             "approver", "by")),
    Column("set_at", ("set at", "date set", "recorded", "created")),
    Column("active", ("active", "in force", "enabled", "current")),
)

CHANGE_COLUMNS = (
    Column("at", ("at", "when", "timestamp", "date"), required=True),
    Column("who", ("who", "user", "changed by", "edited by")),
    Column("record_type", ("record type", "type", "table", "sheet")),
    Column("record_id", ("record id", "record", "id", "reference")),
    Column("field", ("field", "column", "attribute")),
    Column("old_value", ("old value", "was", "previous", "from")),
    Column("new_value", ("new value", "now", "to", "value")),
    Column("reason", ("reason", "why", "note", "remarks")),
)

#: Column order used when this app creates a workbook from scratch, and when it
#: appends a row to an existing sheet that lacks one of these headers.
ISSUANCE_WRITE_ORDER = (
    "employee_number", "item_code", "issued_date", "quantity", "size",
    "issued_by", "cycle_months", "order_id", "notes",
)

ISSUANCE_HEADERS = (
    "Employee Number", "Item Code", "Issued Date", "Quantity", "Size",
    "Issued By", "Cycle Months", "Order ID", "Notes",
)

ORDER_WRITE_ORDER = (
    "order_id", "employee_number", "item_code", "ordered_date", "quantity",
    "supplier_ref", "cancelled", "notes",
)

ORDER_HEADERS = (
    "Order ID", "Employee Number", "Item Code", "Ordered Date", "Quantity",
    "Supplier Ref", "Cancelled", "Notes",
)

OVERRIDE_WRITE_ORDER = (
    "employee_number", "item_code", "next_due", "reason", "authorised_by", "set_at", "active",
)

OVERRIDE_HEADERS = (
    "Employee Number", "Item Code", "Next Due", "Reason", "Authorised By", "Set At", "Active",
)

CHANGE_WRITE_ORDER = (
    "at", "who", "record_type", "record_id", "field", "old_value", "new_value", "reason",
)

CHANGE_HEADERS = (
    "At", "Who", "Record Type", "Record ID", "Field", "Old Value", "New Value", "Reason",
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
