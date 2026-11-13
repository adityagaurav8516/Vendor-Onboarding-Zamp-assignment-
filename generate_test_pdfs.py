import os
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

def create_sample_pdfs():
    output_dir = "test_data/generated_packs"
    os.makedirs(output_dir, exist_ok=True)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=12
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#334155")
    )

    # 1. ABC Technologies - Happy Path Pack
    # Incorporation Certificate
    doc_path = os.path.join(output_dir, "abc-incorporation.pdf")
    doc = SimpleDocTemplate(doc_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = [
        Paragraph("GOVERNMENT OF INDIA", title_style),
        Paragraph("MINISTRY OF CORPORATE AFFAIRS", body_style),
        Spacer(1, 15),
        Paragraph("<b>Certificate of Incorporation</b>", title_style),
        Spacer(1, 10),
        Paragraph("This is to certify that <b>ABC Technologies Pvt Ltd</b> has been incorporated under the Companies Act, 2013.", body_style),
        Spacer(1, 8),
        Paragraph("<b>Registration Number (CIN):</b> U72900DL2015PTC123456", body_style),
        Paragraph("<b>Registered Address:</b> 22 Nehru Place, New Delhi, Delhi 110019", body_style),
        Paragraph("<b>Date of Incorporation:</b> 12th May 2015", body_style)
    ]
    doc.build(story)

    # GST Certificate
    doc_path = os.path.join(output_dir, "abc-gst.pdf")
    doc = SimpleDocTemplate(doc_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = [
        Paragraph("<b>Goods and Services Tax Registration Certificate</b>", title_style),
        Spacer(1, 10),
        Paragraph("<b>Legal Name:</b> ABC Technologies Pvt Ltd", body_style),
        Paragraph("<b>Trade Name:</b> ABC Technologies", body_style),
        Paragraph("<b>GSTIN:</b> 07AABCA1234C1Z3", body_style),
        Paragraph("<b>State:</b> Delhi (07)", body_style),
        Paragraph("<b>Principal Place of Business:</b> 22 Nehru Place, New Delhi, Delhi 110019", body_style)
    ]
    doc.build(story)

    # Bank Cheque / Proof
    doc_path = os.path.join(output_dir, "abc-cheque.pdf")
    doc = SimpleDocTemplate(doc_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = [
        Paragraph("<b>BANK ACCOUNT VERIFICATION CERTIFICATE</b>", title_style),
        Spacer(1, 10),
        Paragraph("<b>Bank Name:</b> HDFC Bank", body_style),
        Paragraph("<b>Branch:</b> Nehru Place Branch, New Delhi", body_style),
        Paragraph("<b>Account Holder Name:</b> ABC Technologies Pvt Ltd", body_style),
        Paragraph("<b>Account Number:</b> 50100987654321", body_style),
        Paragraph("<b>IFSC Code:</b> HDFC0001234", body_style),
        Paragraph("<b>Bank Country:</b> India", body_style)
    ]
    doc.build(story)

    # 2. PQR Enterprises - Payout Fraud Pack (Bank holder is a person)
    doc_path = os.path.join(output_dir, "pqr-incorporation.pdf")
    doc = SimpleDocTemplate(doc_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = [
        Paragraph("GOVERNMENT OF INDIA - MINISTRY OF CORPORATE AFFAIRS", body_style),
        Spacer(1, 10),
        Paragraph("<b>Certificate of Incorporation</b>", title_style),
        Spacer(1, 10),
        Paragraph("<b>Legal Name:</b> PQR Enterprises Pvt Ltd", body_style),
        Paragraph("<b>Registration Number (CIN):</b> U51909MH2018PTC301234", body_style),
        Paragraph("<b>Registered Address:</b> 45 MG Road, Mumbai, Maharashtra 400001", body_style)
    ]
    doc.build(story)

    doc_path = os.path.join(output_dir, "pqr-gst.pdf")
    doc = SimpleDocTemplate(doc_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = [
        Paragraph("<b>Goods and Services Tax Registration Certificate</b>", title_style),
        Spacer(1, 10),
        Paragraph("<b>Legal Name:</b> PQR Enterprises Pvt Ltd", body_style),
        Paragraph("<b>GSTIN:</b> 27AABCP1234F1Z5", body_style),
        Paragraph("<b>Principal Place of Business:</b> 45 MG Road, Mumbai, Maharashtra 400001", body_style)
    ]
    doc.build(story)

    # Fraudulent Cheque Proof (Holder is an individual)
    doc_path = os.path.join(output_dir, "pqr-cheque-person.pdf")
    doc = SimpleDocTemplate(doc_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = [
        Paragraph("<b>BANK ACCOUNT VERIFICATION CERTIFICATE</b>", title_style),
        Spacer(1, 10),
        Paragraph("<b>Bank Name:</b> ICICI Bank", body_style),
        Paragraph("<b>Account Holder Name:</b> Rahul Sharma", body_style),  # Mismatched individual name!
        Paragraph("<b>Account Number:</b> 000401567890", body_style),
        Paragraph("<b>IFSC Code:</b> ICIC0000123", body_style),
        Paragraph("<b>Bank Country:</b> India", body_style)
    ]
    doc.build(story)

    print(f"Successfully generated test PDF packs in '{output_dir}' directory.")

if __name__ == '__main__':
    create_sample_pdfs()