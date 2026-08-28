app_name = "advance_tds_fix"
app_title = "Advance TDS Fix"
app_publisher = "IOH"
app_description = "Excludes GST from the TDS base on Payment Entries raised as advances against a Purchase Order"
app_email = "it@ioh.example"
app_license = "MIT"
app_version = "0.1.0"

doc_events = {
	"Payment Entry": {
		"before_submit": "advance_tds_fix.advance_tds_fix.payment_entry_tds.exclude_gst_from_advance_tds",
	}
}
