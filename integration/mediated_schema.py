"""
The mediated (global) schema and its Global-As-View (GAV) mapping.

The schema matcher *discovers* the correspondences; this file is the *curated*
result that the federation engine actually queries against.  Two virtual views
are exposed to the GUI / SQL box:

    item_master     one row per item code - the "complete history" summary
    item_authenticity   the verdict view (code, verdict, trust_score, red flags)

GAV: each mediated attribute is defined as a view over the local sources.
"""

# mediated attribute -> where it comes from in each local source
GAV_MAPPING = {
    "code": {
        "manufacturer": "manufactured_batches.gtin_serial",
        "distributor": "distributed_stock.item_code",
        "vendor": "purchaseScans.productBarcode",
        "ministry": "counterfeit_reports.suspect_code | verified_genuine_registry.auth_code",
        "note": "auto-discovered global join key (instance-based match, Jaccard on values)",
    },
    "product_name": {
        "manufacturer": "products.brand_name",
        "distributor": "distributed_stock.product_desc",
        "vendor": "purchaseScans.scannedLabelName",
        "note": "representational variance expected (brand vs pack label)",
    },
    "generic_name": {"manufacturer": "products.generic_name"},
    "company": {
        "manufacturer": "companies.company_name",
        "distributor": "inbound_consignments.source_company",
        "note": "must agree - disagreement is a provenance red flag",
    },
    "manufacturer_license": {"manufacturer": "companies.license_no"},
    "factory_location": {"manufacturer": "manufactured_batches.factory_location"},
    "mfg_date": {"manufacturer": "manufactured_batches.mfg_date"},
    "expiry_date": {"manufacturer": "manufactured_batches.expiry_date"},
    "batch_qty": {"manufacturer": "manufactured_batches.batch_qty"},
    "mrp": {
        "manufacturer": "manufactured_batches.mrp",
        "distributor": "distributed_stock.unit_price",
        "vendor": "purchaseScans.sellingPrice",
        "note": "trade-tier price (MRP vs wholesale vs retail) - not auto-merged",
    },
    "distributed_qty": {"distributor": "SUM(distributed_stock.qty_supplied)"},
    "suppliers": {"distributor": "suppliers.sup_name via inbound_consignments"},
    "supplied_to_vendors": {"distributor": "distributed_stock.supplied_to_vendor"},
    "retail_vendors": {"vendor": "vendors.vendorName via purchaseScans"},
    "times_scanned": {"vendor": "COUNT(purchaseScans)"},
    "times_sold": {"vendor": "COUNT(customerSales)"},
    "in_verified_registry": {"ministry": "EXISTS verified_genuine_registry.auth_code"},
    "counterfeit_reports": {"ministry": "counterfeit_reports WHERE suspect_code = code"},
    "enforcement_actions": {"ministry": "enforcement_actions via report_ref"},
}

MEDIATED_VIEWS = {
    "item_master": [
        "code", "product_name", "generic_name", "company", "manufacturer_license",
        "factory_location", "mfg_date", "expiry_date", "batch_qty", "mrp",
        "distributed_qty", "times_scanned", "times_sold",
        "in_verified_registry", "has_counterfeit_report", "lab_result",
    ],
    "item_authenticity": ["code", "verdict", "trust_score", "red_flags"],
}
