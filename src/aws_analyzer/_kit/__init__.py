"""The code every analyzer shares, written once: each service module imports what it uses from these modules.

    fmt      numbers, sizes, money, times and counts: what users type in, and what reports show
    text     escaping for HTML, and clipping and padding for text tables
    deps     optional packages (`_require`) and whether this is a notebook
    errors   AWS error codes, the permission a denied call needs, and `_Hint`

Like the services, these load only the standard library and boto3 / botocore when imported, and they never import a
service module. Nothing here is re-exported: import each name from its own module.
"""
