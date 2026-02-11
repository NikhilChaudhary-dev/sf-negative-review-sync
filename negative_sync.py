import os
import json
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from simple_salesforce import Salesforce
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# ==========================================
# 1. SETUP & CONFIGURATION
# ==========================================
load_dotenv()

# Credentials from Environment/GitHub Secrets
SF_USERNAME = os.getenv('SF_USERNAME')
SF_PASSWORD = os.getenv('SF_PASSWORD')
SF_TOKEN = os.getenv('SF_TOKEN')
DEBOUNCE_API_KEY = os.getenv('DEBOUNCE_API_KEY')
SMARTLEAD_API_KEY = os.getenv('SMARTLEAD_API_KEY', '').strip()

# Target Campaign for Negative Reviews
NEG_REVIEW_CAMPAIGN_ID = "2859924"

# ==========================================
# 2. GOOGLE SHEETS CONNECTION
# ==========================================

def get_tracker_sheet():
    """Connects to your new Negative_Review_Tracker file"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv('GCP_CREDS')
        
        if not creds_json:
            print("❌ Error: GCP_CREDS secret is missing.")
            return None
            
        info = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
        client = gspread.authorize(creds)
        
        # ✅ Aapka Naya Sheet Name
        spreadsheet = client.open("Negative_Review_Tracker")
        sheet = spreadsheet.get_worksheet(0) # Pehla tab uthayega

        # ✅ Set Professional Headers if empty
        if not sheet.get_all_values():
            sheet.append_row([
                "Email", "TimeStamp", "Related Account", "Account Traffic", 
                "Current Tool", "Account Category", "Related Contact Name"
            ])
        return sheet
    except Exception as e:
        print(f"❌ Google Sheet Connection Error: {e}")
        return None

# ==========================================
# 3. SMARTLEAD PUSH (NEGATIVE REVIEW FLOW)
# ==========================================

def push_to_smartlead_neg(email, first, last, acc_name, traffic, tool, category, contact_name):
    """Pushes Negative Review Lead with all SS data points"""
    url = f"https://server.smartlead.ai/api/v1/campaigns/{NEG_REVIEW_CAMPAIGN_ID}/leads"
    params = {"api_key": SMARTLEAD_API_KEY}
    
    payload = {
        "lead_list": [{
            "email": email.strip(),
            "first_name": (first or "").strip(),
            "last_name": (last or "").strip(),
            "custom_fields": {
                "source": "Salesforce Negative Review",
                "related_account_id": acc_name, # Account Name mapping
                "account_traffic": traffic,
                "current_tool": tool,
                "account_category": category,
                "related_contact_name": contact_name
            }
        }],
        "settings": { "ignore_duplicate_leads_in_other_campaign": False }
    }
    
    try:
        res = requests.post(url, params=params, json=payload)
        return res.status_code in [200, 201]
    except Exception as e:
        print(f"   ⚠️ Smartlead API Fail: {e}")
        return False

# ==========================================
# 4. MAIN SYNC LOGIC
# ==========================================

def run_sync():
    print(f"🚀 Starting Negative Review Sync: {datetime.now()}")
    
    # 1. Salesforce Login
    try:
        sf = Salesforce(username=SF_USERNAME, password=SF_PASSWORD, security_token=SF_TOKEN)
    except Exception as e:
        print(f"❌ Salesforce Login Failed: {e}"); return

    # 2. Sheet Access
    sheet = get_tracker_sheet()
    if not sheet: return

    # Avoid re-processing leads
    processed_emails = [str(e).lower() for e in sheet.col_values(1) if e]

    # ✅ QUERY: Outbound + Negative Review
    # Includes formula and lookup fields from screenshots
    check_time = datetime.now(timezone.utc) - timedelta(weeks=4)
    query = f"""
        SELECT Id, Email, FirstName, LastName, 
               Account_Traffic__c, Account_s_Current_Tool__c, Account_Primary_Category__c,
               Related_Account__r.Name, Related_Contact__r.Name
        FROM Lead 
        WHERE CreatedDate > {check_time.strftime('%Y-%m-%dT%H:%M:%SZ')}
        AND LeadSource = 'Outbound'
        AND Sub_Channel__c = 'Negative Review'
        AND (Owner.Name = 'Vipul Babbar' OR Owner.Name = 'Anirudh Vashishth')
    """
    
    leads = sf.query(query).get('records', [])
    print(f"📄 Found {len(leads)} Negative Review leads.")

    for lead in leads:
        email = (lead.get('Email') or '').lower().strip()
        if not email or email in processed_emails: continue

        # Fetching data points exactly from your Screenshots
        acc_name = lead.get('Related_Account__r', {}).get('Name') if lead.get('Related_Account__r') else lead.get('Company', 'N/A')
        traffic = lead.get('Account_Traffic__c', 'N/A')
        tool = lead.get('Account_s_Current_Tool__c', 'N/A')
        category = lead.get('Account_Primary_Category__c', 'N/A')
        contact_name = lead.get('Related_Contact__r', {}).get('Name') if lead.get('Related_Contact__r') else 'N/A'

        print(f"⚡ Processing: {email} | Account: {acc_name}")
        
        # 3. Debounce Validation
        v_res = requests.get("https://api.debounce.io/v1/", params={'api': DEBOUNCE_API_KEY, 'email': email}).json()
        
        if v_res.get('debounce', {}).get('result') in ['Accept All', 'Deliverable', 'Safe to Send']:
            # 4. Smartlead Push
            if push_to_smartlead_neg(email, lead.get('FirstName'), lead.get('LastName'), 
                                     acc_name, traffic, tool, category, contact_name):
                
                # 5. Log to New Spreadsheet
                sheet.append_row([
                    email, 
                    datetime.now().isoformat(), 
                    acc_name, 
                    traffic, 
                    tool, 
                    category, 
                    contact_name
                ])
                processed_emails.append(email)

    print(f"✅ Negative Review Sync Finished.")

if __name__ == "__main__":
    run_sync()
