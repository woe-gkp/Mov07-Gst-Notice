import io
import json
import os
from datetime import date
from dotenv import load_dotenv
from docxtpl import DocxTemplate
from google import genai
from google.genai import types
from PIL import Image
import streamlit as st

load_dotenv()

st.set_page_config(page_title="MOV-07 Notice Automation", layout="wide", page_icon="⚖️")
st.title("⚖️ FORM GST MOV-07 Notice Generator")
st.markdown("उत्तर प्रदेश राज्य कर (सचल दल इकाई) हेतु स्वचालित कारण बताओ नोटिस प्रणाली")

st.sidebar.header("🔑 API Settings")
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
  api_key = st.sidebar.text_input("Gemini API Key दर्ज करें:", type="password")
if not api_key:
  st.sidebar.warning("⚠️ API Key Missing")
  st.info("कृपया साइडबार में API Key डालें या .env फ़ाइल में GEMINI_API_KEY सेट करें।")
  st.stop()

client = None
try:
  client = genai.Client(api_key=api_key)
  st.sidebar.success("✅ API Successfully Connected")
except Exception as e:
  st.sidebar.error(f"API Client Initialization Error: {e}")
  st.stop()

GEMINI_MODEL = "models/gemini-3.5-flash"


# ============================================================
# --- AI HELPERS ---
# ============================================================

def _files_to_parts(files):
  parts = []
  for f in files or []:
    if f.type == "application/pdf":
      parts.append(types.Part.from_bytes(data=f.read(), mime_type="application/pdf"))
    else:
      parts.append(Image.open(f))
  return parts


def gemini_json(prompt, files=None, temperature=0.15):
  contents = _files_to_parts(files) + [prompt]
  response = client.models.generate_content(
      model=GEMINI_MODEL, contents=contents,
      config=types.GenerateContentConfig(response_mime_type="application/json", temperature=temperature),
  )
  return json.loads(response.text)


def gemini_text(prompt, files=None, temperature=0.2):
  contents = _files_to_parts(files) + [prompt]
  response = client.models.generate_content(
      model=GEMINI_MODEL, contents=contents,
      config=types.GenerateContentConfig(temperature=temperature),
  )
  return response.text.strip()


def extract_driver_statement(files) -> dict:
  prompt = """
  आप उत्तर प्रदेश राज्य कर विभाग के सचल दल इकाई के अधिकारी सहायक हैं। संलग्न वाहन चालक के ब्यान
  (Driver Statement) को पढ़ें और निम्न JSON संरचना में डेटा निकालें:
  {
    "firm_or_driver_name": "व्यापारी/फर्म/चालक का नाम",
    "gstin": "GSTIN यदि उल्लेखित हो",
    "vehicle_no": "वाहन संख्या",
    "interception_place": "रोके जाने का स्थान",
    "interception_date": "रोके जाने की तिथि (DD/MM/YYYY)",
    "interception_time": "रोके जाने का समय",
    "goods_description": "माल का विवरण",
    "place_of_loading": "लोडिंग/प्रस्थान स्थान",
    "place_of_delivery": "डिलेवरी/गंतव्य स्थान",
    "docs_available_at_site": true/false,
    "docs_mentioned_details": "यदि प्रपत्र उपलब्ध होने का उल्लेख है तो संक्षिप्त विवरण, अन्यथा खाली"
  }
  यदि कोई फ़ील्ड उपलब्ध न हो तो खाली स्ट्रिंग "" या false दें। केवल valid JSON लौटाएं।
  """
  return gemini_json(prompt, files, temperature=0.1)


def compose_case_details_para(ext: dict, inspection_type: str) -> str:
  prompt = f"""
  आप उत्तर प्रदेश राज्य कर विभाग के लिए GST FORM MOV-07 नोटिस का बिन्दु-1 "मामले का विवरणः-" लिख रहे हैं।
  शैली संकेत: "प्रश्नगत वाहन को [जाँच का प्रकार] जाँच हेतु दिनांक [तिथि] समय [समय] पर वाहन में लदे [माल]
  का परिवहन करते हुए [स्थान] जनपद बागपत में रोका गया। वाहन को रोके जाने के उपरान्त वाहन चालक द्वारा
  वाहन में लोड माल के परिवहन से सम्बन्धित वाहन चालक (PERSON IN CHARGE OF THE CONVEYANCE) का ब्यान उसी
  समय प्रापर आफिसर द्वारा दर्ज किया गया, जिसमें इनके द्वारा बताया गया कि वह अपने वाहन में माल- [माल]
  लोड करके [लोडिंग स्थान] से [डिलेवरी स्थान] जा रहा है।"

  डेटा: {json.dumps(ext, ensure_ascii=False)}
  जाँच का प्रकार: {inspection_type}

  ध्यान रहे — यह पैरा केवल यह बताता है कि रोके जाने पर चालक का बयान क्या था; रोके जाने के समय या बाद
  में कोई प्रपत्र दिए गए या नहीं, इसका उल्लेख इस पैरा में न करें (वह अगले बिन्दु में अलग से आएगा)।
  4-5 वाक्यों का स्वाभाविक, औपचारिक हिन्दी पैरा लिखें। केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt)


def compose_docs_para(raw_details: str, files, stage_label: str, case_context: dict) -> str:
  if not raw_details.strip() and not files:
    return "कोई प्रपत्र प्रस्तुत नहीं किया गया।"
  prompt = f"""
  आप GST MOV-07 नोटिस के लिए "{stage_label}" चरण में प्रस्तुत प्रपत्रों (बिल/इनवॉइस/डिलेवरी चालान/
  ई-वे बिल) का विवरण एक पूर्ण, विस्तृत, औपचारिक हिन्दी पैराग्राफ में लिख रहे हैं। संलग्न फोटो/PDF एवं
  निम्न संक्षिप्त नोट से सारा डेटा निकालें और अवश्य शामिल करें (जो भी उपलब्ध हो):
  - विक्रेता फर्म का नाम एवं GSTIN, क्रेता फर्म का नाम एवं GSTIN
  - बिल/इनवॉइस/डिलेवरी चालान संख्या एवं दिनांक, ई-वे बिल संख्या, दिनांक एवं समय
  - माल का पूर्ण विवरण एवं HSN कोड, मात्रा (वजन/नग/इकाई)
  - करयोग्य घोषित मूल्य, कर की दर (%) एवं कर की राशि (IGST अथवा CGST+SGST अलग-अलग)
  - माल के परिवहन का उद्गम स्थान एवं गंतव्य स्थान

  संक्षिप्त नोट: "{raw_details}"
  केस संदर्भ: {json.dumps(case_context, ensure_ascii=False)}

  5-8 वाक्यों में पूरा, सटीक, विस्तृत पैरा लिखें (सामान्य/अधूरा न लगे)। स्पष्ट लिखें कि यह प्रपत्र
  "{stage_label}" प्रस्तुत किए गए। केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt, files)


def compose_initial_ground_order_para(inspection_type: str, case_context: dict, docs_at_stop: bool) -> str:
  produced_clause = (
      "वाहन चालक द्वारा वाहन में लदे माल से सम्बन्धित उपरोक्त प्रपत्र उसी समय प्रस्तुत किए गए।"
      if docs_at_stop else
      "वाहन चालक द्वारा वाहन में लदे माल से सम्बन्धित कोई भी प्रपत्र (बिल/इनवॉइस/डिलेवरी चालान/ई-वे "
      "बिल/बिल्टी आदि) प्रस्तुत नहीं किया गया।"
  )
  violation_clause = (
      "" if docs_at_stop else
      " प्रश्नगत प्रकरण में वाहन चालक द्वारा उपरोक्त में से कोई भी प्रपत्र प्रस्तुत न किये जाने के "
      "कारण उक्त प्राविधानों का स्पष्ट उल्लंघन पाया गया।"
  )
  prompt = f"""
  GST MOV-07 नोटिस के बिन्दु "वाहन को रोके जाने का प्रारम्भिक आधार एवं भौतिक सत्यापन का आदेशः-" हेतु
  पैरा लिखें। यह ध्यान रहे कि यह पैरा केवल "रोके जाने के क्षण" की स्थिति बताता है — बाद में (भौतिक
  सत्यापन से पूर्व अथवा बाद में) कोई प्रपत्र प्रस्तुत हुआ हो तो उसका उल्लेख यहाँ बिल्कुल न करें।

  शैली: "वाहन को {inspection_type} रोके जाने पर {produced_clause} CGST/UPGST Act, 2017 की धारा 68(1)
  में यह व्यवस्था है कि prescribed value से अधिक goods carrying conveyance का person-in-charge
  prescribed documents को माल के परिवहन किये जाने के समय रखा जाना अनिवार्य है। धारा 68(3) proper
  officer को interception के समय documents और devices प्रस्तुत कराने तथा goods inspection करने का
  अधिकार देती है। जीएसटी अधिनियम 2017 के Rule 138A(1) के अनुसार person-in-charge को invoice या bill
  of supply या delivery challan, तथा e-way bill की physical/electronic copy अथवा e-way bill
  number/RFID mapping carry करना आवश्यक है।{violation_clause} अतः उक्त के आधार पर वाहन में लदे माल का
  भौतिक सत्यापन हेतु मूव-02 जारी किया गया।"

  केस संदर्भ: {json.dumps(case_context, ensure_ascii=False)}
  इसी भाव के अनुसार पूर्ण, स्वाभाविक हिन्दी पैरा लिखें। केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt)


def compose_physical_verification_para(docs_match: str, found_goods: str, stage1_timing: str, docs_context: str) -> str:
  if docs_match == "match" and stage1_timing == "at_stop":
    hint = "भौतिक सत्यापन में माल प्रस्तुत प्रपत्रों (जो रोके जाने के समय ही प्रस्तुत किए गए थे) के अनुसार सही पाया गया।"
  elif docs_match == "match" and stage1_timing == "before_verification":
    hint = "भौतिक सत्यापन से पूर्व प्रस्तुत प्रपत्रों के अनुसार ही वाहन में माल लदा पाया गया है।"
  elif docs_match == "mismatch":
    hint = "भौतिक सत्यापन में पाया गया माल प्रस्तुत प्रपत्रों में घोषित माल से भिन्न पाया गया।"
  else:
    hint = (
        "भौतिक सत्यापन में माल पाया गया किन्तु उक्त माल से सम्बन्धित कोई बिल/इनवॉइस/ई-वे बिल प्रस्तुत "
        "न होने के कारण माल एवं वाहन को डिटेन करते हुए अग्रिम कार्यवाही किया जाना अपेक्षित है।"
    )
  prompt = f"""
  GST MOV-07 नोटिस के बिन्दु "भौतिक सत्यापन का विवरणः-" हेतु एक पूर्ण औपचारिक हिन्दी पैरा लिखें।
  शैली संकेत: "{hint}"
  भौतिक सत्यापन में जो माल मिला उसका विवरण (यदि दिया गया हो): "{found_goods}"
  प्रपत्र सन्दर्भ: "{docs_context}"
  3-5 वाक्यों में पूरा पैरा लिखें, केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt)


def expand_defect_paragraph(heading: str, full_case_context: dict) -> str:
  prompt = f"""
  आप GST MOV-07 नोटिस में एक "अन्य कमी" का पूरा उप-पैराग्राफ लिख रहे हैं।
  कमी का शीर्षक: "{heading}"
  पूरे केस का सन्दर्भ (इसी के विशिष्ट तथ्यों — फर्म नाम, GSTIN, बिल/ईवेबिल संख्या, माल, HSN, मूल्य,
  स्थान आदि — का उपयोग करते हुए पैरा लिखें, सामान्य/जेनरिक पैरा न लिखें):
  {json.dumps(full_case_context, ensure_ascii=False, default=str)}
  3-5 वाक्यों का एक पूर्ण, ठोस, कानूनी हिन्दी पैराग्राफ लिखें जो सीधे नोटिस में प्रयोग हो सके — अधूरा
  या सामान्य कतई न लगे। केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt, temperature=0.3)


def fetch_judicial_precedents(defects_summary: str, count: int = 3) -> list:
  prompt = f"""
  आप उत्तर प्रदेश राज्य कर विभाग के लिए GST विधि शोधकर्ता हैं। निम्न कमियों/आधारों का विश्लेषण करें:
  "{defects_summary}"
  Section 129, e-way bill त्रुटि, गंतव्य/HSN भिन्नता, या मूल्यांकन सम्बन्धी {count} अत्यंत प्रासंगिक
  न्यायिक निर्णय (Allahabad High Court अथवा Supreme Court of India) खोजें।
  Return strictly JSON: {{"judgments": [{{"case_name": "...", "court_and_year": "इलाहाबाद उच्च
  न्यायालय, 20XX", "ratio_decidendi": "हिन्दी में निर्णय का सार", "relevance_to_case": "यह मामले पर
  कैसे लागू होता है, हिन्दी में"}}]}} — ठीक {count} objects।
  """
  data = gemini_json(prompt, temperature=0.2)
  return data.get("judgments", [])


def compose_ownership_valuation_para(pc: dict) -> str:
  """बिन्दु-8 'माल के स्वामित्व एवं मूल्यांकन से सम्बन्धित विवरण' — दावेदार कौन है, स्वामी स्वीकार
  हुआ या नहीं (कारण सहित), बिल स्वीकार हुआ या नहीं (कारण सहित)।"""
  prompt = f"""
  नीचे दिए डेटा के आधार पर GST MOV-07 नोटिस के बिन्दु "माल के स्वामित्व एवं मूल्यांकन से सम्बन्धित
  विवरणः-" का एक पूर्ण, स्पष्ट हिन्दी पैरा लिखें, जिसमें क्रमशः बताया जाए:
  1. स्वामित्व का दावा किसने प्रस्तुत किया, नाम क्या है
  2. क्या उसे स्वामी स्वीकार किया गया — यदि नहीं तो कारण सहित विस्तृत उल्लेख
  3. (यदि स्वामी स्वीकार है) क्या प्रस्तुत बिल को स्वीकार किया गया — यदि नहीं तो कारण सहित विस्तृत उल्लेख

  डेटा: {json.dumps(pc, ensure_ascii=False, default=str)}

  4-6 वाक्यों का पूरा, तथ्यपरक पैरा लिखें। केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt, temperature=0.2)


def compose_tax_penalty_para(pc: dict) -> str:
  prompt = f"""
  नीचे दिए गणना-डेटा के आधार पर GST MOV-07 नोटिस के बिन्दु "लागू जुर्माने की गणना" का एक पूर्ण, सटीक
  पैरा हिन्दी में लिखें। आँकड़ों में कोई बदलाव न करें, केवल उन्हें वाक्य में औपचारिक रूप से पिरोएं:
  {json.dumps(pc, ensure_ascii=False, default=str)}

  उदाहरण शैली (129(1)(a), बिल आधारित): "...वाहन में लदे माल ... कुल ... जिसका घोषित मूल्य रुपये ...
  पर ... प्रतिशत कर की दर के अनुसार सीजीएसटी में रुपये ... एवं एसजीएसटी में रुपये ... की पैनाल्टी की
  गणना जीएसटी अधिनियम 2017 की धारा 129(1)(a) के अन्तर्गत करते हुए नोटिस जारी किया जा रहा है।"

  उदाहरण शैली (129(1)(a), मूल्यांकित मूल्य आधारित — बिल अस्वीकृत किन्तु स्वामी स्वीकृत): "...चूंकि
  प्रस्तुत बिल स्वीकार्य नहीं पाया गया, अतः वाहन में लदे माल की गुणवत्ता एवं प्रचलित बाजार भाव रुपये
  ... प्रति ... के अनुसार कुल आंकलित करयोग्य मूल्य रुपये ... पर ... प्रतिशत कर दर से सीजीएसटी रुपये ...
  एवं एसजीएसटी रुपये ... की पैनाल्टी की गणना धारा 129(1)(a) के अन्तर्गत की जा रही है।"

  उदाहरण शैली (129(1)(b), स्वामी अस्वीकृत): "...चूंकि माल के स्वामित्व का दावा स्वीकार नहीं किया गया,
  अतः वाहन में लदे माल की गुणवत्ता एवं प्रचलित बाजार भाव रुपये ... प्रति ... के अनुसार कुल मूल्य रुपये
  ... आंकलित करते हुए धारा 129(1)(b) के अन्तर्गत कुल रुपये ... की पैनाल्टी की गणना करते हुए नोटिस जारी
  किया जा रहा है।"

  4-6 वाक्यों का पूरा पैरा लिखें। केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt, temperature=0.15)


def suggest_notice_gaps(context: dict) -> list:
  prompt = f"""
  एक वरिष्ठ GST विधि समीक्षक के रूप में नीचे दिए MOV-07 नोटिस डेटा की समीक्षा करें और बताएं कि कहाँ-कहाँ
  विवरण अधूरा, असंगत या कमजोर लग रहा है। डेटा: {json.dumps(context, ensure_ascii=False, default=str)}
  Return strictly JSON: {{"gaps": ["सुझाव 1", ...]}} (अधिकतम 6, प्रत्येक एक पंक्ति में संक्षिप्त)।
  """
  data = gemini_json(prompt, temperature=0.2)
  return data.get("gaps", [])


def expand_gap_to_paragraph(gap_text: str, case_context: dict) -> str:
  prompt = f"""
  नीचे GST MOV-07 नोटिस की समीक्षा में मिला एक सुझाव/कमी है:
  "{gap_text}"
  केस संदर्भ: {json.dumps(case_context, ensure_ascii=False, default=str)}
  इसे नोटिस में "अन्य कमियों" के एक उप-बिन्दु के रूप में जोड़ने हेतु 3-4 वाक्यों का पूर्ण, ठोस, औपचारिक
  हिन्दी पैराग्राफ लिखें। केवल पैरा टेक्स्ट लौटाएं।
  """
  return gemini_text(prompt, temperature=0.25)


# ============================================================
# --- SESSION STATE DEFAULTS (सभी AI-सम्पादन योग्य टेक्स्ट सीधे widget key में रखे जाते हैं) ---
# ============================================================
WIDGET_DEFAULTS = {
    "ext": {}, "case_details_box": "", "docs_stage1_box": "",
    "physical_verification_box": "", "initial_ground_order_box": "",
    "later_docs_box": "", "ownership_rejection_box": "", "bill_rejection_box": "",
    "defect_count": 1, "notice_gaps": [], "gap_paragraphs": {}, "gap_selected": {},
    "show_review": False,
}
for k, v in WIDGET_DEFAULTS.items():
  if k not in st.session_state:
    st.session_state[k] = v

# ============================================================
# STEP 1 — वाहन चालक का ब्यान
# ============================================================
st.header("1️⃣ वाहन चालक का ब्यान")
statement_files = st.file_uploader(
    "वाहन चालक का ब्यान अपलोड करें (PDF / JPG / PNG):",
    type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True, key="statement_files",
)
if statement_files and st.button("⚡ ब्यान से विवरण स्वतः पढ़ें"):
  with st.spinner("ब्यान पढ़ा जा रहा है..."):
    try:
      st.session_state.ext = extract_driver_statement(statement_files)
      st.success("ब्यान से विवरण पढ़ लिया गया।")
    except Exception as e:
      st.error(f"त्रुटि: {e}")
ext = st.session_state.ext

# ============================================================
# STEP 2 — शीर्ष विवरण
# ============================================================
st.header("2️⃣ नोटिस किसके नाम — शीर्ष विवरण")
c1, c2, c3 = st.columns(3)
with c1:
  firm_or_driver_name = st.text_input(
      "फर्म / वाहन चालक का नाम*", value=ext.get("firm_or_driver_name", "[नाम उपलब्ध नहीं]")
  )
  gstin = st.text_input("GSTIN (उपलब्ध न हो तो खाली छोड़ें)", value=ext.get("gstin", ""))
  is_up_gstin = gstin.strip().startswith("09") if gstin.strip() else None
  if gstin.strip() and not is_up_gstin:
    st.info("ℹ️ GSTIN उत्तर प्रदेश से बाहर का प्रतीत होता है — TMP ID भी दर्ज करें।")
  tmp_id = st.text_input(
      "TMP ID (यदि GSTIN उपलब्ध नहीं या UP से बाहर का है)",
      value="" if gstin.strip() and is_up_gstin else "[TMP ID उपलब्ध नहीं]",
  )
with c2:
  case_id = st.text_input("Case ID*", value="[Case ID उपलब्ध नहीं]")
  notice_no = st.text_input("Notice No.*", value="[Notice No. उपलब्ध नहीं]")
  notice_date = st.date_input("Notice Date", value=date.today())
with c3:
  vehicle_no = st.text_input("Vehicle No.* (ब्यान से मिल जाएगा)", value=ext.get("vehicle_no", ""))
  officer_unit = st.text_input("सचल दल इकाई स्थान", value="बागपत")
  officer_name = st.text_input("अधिकारी का नाम", value="")
  officer_designation = st.text_input("पदनाम", value="सहायक आयुक्त, राज्य कर")

multi_party = st.checkbox("क्या ट्रांसपोर्टर/वाहन स्वामी अलग से नोटिस में जोड़ने हैं?", value=False)
transporter_name, vehicle_owner_name = "", ""
if multi_party:
  cc1, cc2 = st.columns(2)
  with cc1:
    transporter_name = st.text_input("ट्रांसपोर्टर का नाम/पता")
  with cc2:
    vehicle_owner_name = st.text_input("वाहन स्वामी का नाम/पता")

# ============================================================
# STEP 3 — जाँच का प्रकार व मामले का विवरण
# ============================================================
st.header("3️⃣ जाँच का प्रकार एवं मामले का विवरण")
inspection_type = st.selectbox(
    "जाँच का प्रकार:",
    ["विशेष जाँच अभियान के दौरान", "नियमित जाँच", "गोपनीय सूचना के आधार पर",
     "मुख्यालय द्वारा प्रेषित सूचना पर"],
)
interception_date = st.date_input("रोके जाने की तिथि", value=date.today())
interception_time = st.text_input("रोके जाने का समय", value=ext.get("interception_time", ""))
interception_place = st.text_input("रोके जाने का स्थान", value=ext.get("interception_place", ""))
goods_description = st.text_input("माल का विवरण", value=ext.get("goods_description", ""))
place_of_loading = st.text_input("लोडिंग/उद्गम स्थान", value=ext.get("place_of_loading", ""))
place_of_delivery = st.text_input("डिलेवरी/गंतव्य स्थान", value=ext.get("place_of_delivery", ""))

if st.button("✨ AI से 'मामले का विवरण' पैरा तैयार करें"):
  with st.spinner("पैरा तैयार किया जा रहा है..."):
    try:
      merged_ext = {**ext, "goods_description": goods_description,
                    "place_of_loading": place_of_loading, "place_of_delivery": place_of_delivery,
                    "interception_place": interception_place, "interception_time": interception_time,
                    "interception_date": str(interception_date)}
      st.session_state.case_details_box = compose_case_details_para(merged_ext, inspection_type)
    except Exception as e:
      st.error(f"त्रुटि: {e}")
case_details_para = st.text_area("मामले का विवरण (संपादन योग्य):", key="case_details_box", height=140)

# ============================================================
# STEP 4 — प्रस्तुत/प्राप्त प्रपत्रों का विवरण
# ============================================================
st.header("4️⃣ प्रस्तुत/प्राप्त प्रपत्रों का विवरण")
docs_at_site = st.radio("क्या वाहन रोके जाने के समय ही कोई प्रपत्र प्रस्तुत किए गए?", ["नहीं", "हाँ"])

docs_before_verification = "नहीं"
if docs_at_site == "नहीं":
  docs_before_verification = st.radio(
      "क्या भौतिक सत्यापन से पूर्व (रोके जाने के बाद) कोई प्रपत्र प्रस्तुत किए गए?", ["नहीं", "हाँ"]
  )

if docs_at_site == "हाँ":
  stage1_timing = "at_stop"
  stage1_label = "वाहन रोके जाने के समय"
  docs_stage1_heading = "प्रस्तुत/प्राप्त प्रपत्रों का विवरणः-"
elif docs_before_verification == "हाँ":
  stage1_timing = "before_verification"
  stage1_label = "भौतिक सत्यापन से पूर्व"
  docs_stage1_heading = "भौतिक सत्यापन से पूर्व प्रस्तुत प्रपत्रों का विवरणः-"
else:
  stage1_timing = "none"
  stage1_label = ""
  docs_stage1_heading = "प्रस्तुत/प्राप्त प्रपत्रों का विवरणः-"

docs_stage1_present = stage1_timing != "none"

if docs_stage1_present:
  s1_raw = st.text_area(f"{stage1_label} प्रस्तुत प्रपत्रों का संक्षिप्त नोट:", key="s1_raw")
  s1_files = st.file_uploader(
      f"{stage1_label} प्रस्तुत बिल/ईवेबिल की फोटो/PDF:", type=["pdf", "jpg", "jpeg", "png"],
      accept_multiple_files=True, key="s1_files",
  )
  if st.button("✨ AI द्वारा प्रपत्र-पैरा तैयार करें", key="btn_s1"):
    with st.spinner("पैरा तैयार किया जा रहा है..."):
      try:
        case_ctx = {"goods_description": goods_description, "vehicle_no": vehicle_no,
                    "place_of_loading": place_of_loading, "place_of_delivery": place_of_delivery}
        st.session_state.docs_stage1_box = compose_docs_para(s1_raw, s1_files, stage1_label, case_ctx)
      except Exception as e:
        st.error(f"त्रुटि: {e}")
  docs_stage1_para = st.text_area("प्रपत्रों का पैरा (संपादन योग्य):", key="docs_stage1_box", height=140)
else:
  docs_stage1_para = (
      "वाहन को रोके जाने के समय वाहन चालक द्वारा माल से सम्बन्धित कोई भी प्रपत्र (ट्रांसपोर्ट का विवरण, "
      "विक्रेता व्यापारी का विवरण, क्रेता व्यापारी का विवरण, इनवॉइस, ई-वे बिल इत्यादि) प्रस्तुत नहीं किया गया।"
  )
  st.info(docs_stage1_para)

# ============================================================
# STEP 5 — भौतिक सत्यापन का विवरण
# ============================================================
st.header("5️⃣ भौतिक सत्यापन का विवरण")
if docs_stage1_present:
  match_choice = st.radio("क्या भौतिक सत्यापन में माल प्रस्तुत प्रपत्रों के अनुसार मिला?", ["हाँ", "नहीं"])
  if match_choice == "हाँ":
    docs_match, found_goods = "match", ""
  else:
    docs_match = "mismatch"
    found_goods = st.text_input("भौतिक सत्यापन में वास्तव में क्या माल मिला (विवरण/मात्रा):")
else:
  docs_match = "no_docs"
  found_goods = st.text_input("भौतिक सत्यापन में क्या माल मिला (विवरण/मात्रा/यूनिट):", value=goods_description)

if st.button("✨ AI से भौतिक सत्यापन पैरा तैयार करें"):
  with st.spinner("पैरा तैयार किया जा रहा है..."):
    try:
      st.session_state.physical_verification_box = compose_physical_verification_para(
          docs_match, found_goods, stage1_timing, docs_stage1_para
      )
    except Exception as e:
      st.error(f"त्रुटि: {e}")
physical_verification_para = st.text_area(
    "भौतिक सत्यापन पैरा (संपादन योग्य):", key="physical_verification_box", height=120,
)

# ============================================================
# STEP 6 — प्रारम्भिक आधार एवं भौतिक सत्यापन आदेश
# ============================================================
st.header("6️⃣ प्रारम्भिक आधार एवं भौतिक सत्यापन का आदेश")
if st.button("✨ AI से यह पैरा तैयार करें"):
  with st.spinner("पैरा तैयार किया जा रहा है..."):
    try:
      case_ctx = {"goods_description": goods_description, "interception_place": interception_place,
                  "interception_date": str(interception_date), "interception_time": interception_time}
      st.session_state.initial_ground_order_box = compose_initial_ground_order_para(
          inspection_type, case_ctx, docs_at_stop=(stage1_timing == "at_stop")
      )
    except Exception as e:
      st.error(f"त्रुटि: {e}")
initial_ground_order_para = st.text_area(
    "पैरा (संपादन योग्य):", key="initial_ground_order_box", height=120,
)

# ============================================================
# STEP 7 — भौतिक सत्यापन के उपरान्त प्रस्तुत प्रपत्र
# ============================================================
st.header("7️⃣ भौतिक सत्यापन के उपरान्त प्रस्तुत प्रपत्र")
docs_after_verification = st.radio("क्या भौतिक सत्यापन के उपरान्त कोई प्रपत्र प्रस्तुत किए गए?", ["नहीं", "हाँ"])
docs_after_present = docs_after_verification == "हाँ"
if docs_after_present:
  s2_raw = st.text_area("भौतिक सत्यापन उपरान्त प्रस्तुत प्रपत्रों का संक्षिप्त नोट:", key="s2_raw")
  s2_files = st.file_uploader(
      "प्रस्तुत बिल/ईवेबिल की फोटो/PDF:", type=["pdf", "jpg", "jpeg", "png"],
      accept_multiple_files=True, key="s2_files",
  )
  if st.button("✨ AI द्वारा प्रपत्र-पैरा तैयार करें", key="btn_s2"):
    with st.spinner("पैरा तैयार किया जा रहा है..."):
      try:
        case_ctx = {"goods_description": goods_description, "vehicle_no": vehicle_no}
        st.session_state.later_docs_box = compose_docs_para(
            s2_raw, s2_files, "भौतिक सत्यापन के उपरान्त", case_ctx
        )
      except Exception as e:
        st.error(f"त्रुटि: {e}")
  docs_after_verification_para = st.text_area("पैरा (संपादन योग्य):", key="later_docs_box", height=120)
else:
  docs_after_verification_para = ""

# ============================================================
# STEP 8 — स्वामित्व का दावा
# ============================================================
st.header("8️⃣ माल के स्वामित्व का दावा")
claimant = st.selectbox("किसने स्वामित्व का दावा प्रस्तुत किया?",
                         ["क्रेता (Purchaser)", "विक्रेता (Seller)", "वाहन चालक", "कोई नहीं"])
if claimant != "कोई नहीं":
  ownership_accepted = st.radio("क्या दावेदार को माल का स्वामी स्वीकार किया गया?", ["हाँ", "नहीं"]) == "हाँ"
else:
  ownership_accepted = False

ownership_rejection_reason = ""
if not ownership_accepted:
  ownership_rejection_reason = st.text_input(
      "स्वामी स्वीकार न करने का कारण:",
      value="किसी भी पक्ष द्वारा स्वामित्व का दावा नहीं किया गया।" if claimant == "कोई नहीं" else "",
  )

# ============================================================
# STEP 9 — बिल एवं मूल्य की स्वीकृति (केवल यदि स्वामी स्वीकृत — क्लॉज़ 129(1)(a) दोनों ही स्थिति में लागू)
# ============================================================
bill_accepted = False
bill_rejection_reason = ""
if ownership_accepted:
  st.header("9️⃣ बिल एवं मूल्य की स्वीकृति")
  bill_accepted = st.radio("क्या प्रस्तुत बिल/प्रपत्रों को स्वीकार किया गया?", ["हाँ", "नहीं"]) == "हाँ"
  if not bill_accepted:
    bill_rejection_reason = st.text_input(
        "बिल अस्वीकार करने का कारण:",
        value="प्रपत्रों में घोषित विवरण एवं परिवहन में भिन्नता",
    )

# क्लॉज़ केवल स्वामित्व-स्वीकृति पर आधारित है; बिल-स्वीकृति सिर्फ पैनल्टी की गणना-पद्धति तय करती है
penalty_clause = "129(1)(a)" if ownership_accepted else "129(1)(b)"
if penalty_clause == "129(1)(a)":
  st.success(
      "👉 लागू नियम: धारा 129(1)(a) — स्वामी स्वीकृत होने से यह क्लॉज़ लागू है। "
      + ("पेनल्टी बिल के अनुसार स्वतः गणना होगी।" if bill_accepted
         else "बिल अस्वीकृत होने से पेनल्टी आंकलित (मार्केट) मूल्य के अनुसार स्वतः गणना होगी।")
  )
else:
  st.warning("👉 लागू नियम: धारा 129(1)(b) — स्वामी अस्वीकृत, पेनल्टी राशि मैन्युअल दर्ज करें।")

# ============================================================
# STEP 10 — टैक्स/पेनल्टी इनपुट
# ============================================================
st.header("🔟 टैक्स एवं पेनल्टी इनपुट")
bill_taxable_value = tax_rate_pct = bill_tax_amount = 0.0
assessed_goods_value = market_rate_per_unit = 0.0
unit_type, tax_type = "", "CGST+SGST"
penalty_amount = 0.0

if penalty_clause == "129(1)(a)" and bill_accepted:
  colp1, colp2, colp3 = st.columns(3)
  with colp1:
    bill_taxable_value = st.number_input("बिल में घोषित करयोग्य मूल्य (₹)", min_value=0.0, value=0.0)
  with colp2:
    tax_rate_pct = st.number_input("कर की दर (%)", min_value=0.0, value=18.0)
  with colp3:
    tax_type = st.selectbox("कर प्रकार", ["CGST+SGST", "IGST"])
  bill_tax_amount = round(bill_taxable_value * tax_rate_pct / 100, 2)
  penalty_amount = round(bill_tax_amount * 2, 2)
  assessed_goods_value = bill_taxable_value
  st.info(f"मूल कर राशि ≈ ₹{bill_tax_amount:,.2f} — धारा 129(1)(a) अंतर्गत पेनल्टी (200% कर) = कुल ₹{penalty_amount:,.2f}")

elif penalty_clause == "129(1)(a)" and not bill_accepted:
  colp1, colp2, colp3 = st.columns(3)
  with colp1:
    market_rate_per_unit = st.number_input("प्रचलित बाजार भाव (₹ प्रति इकाई)", min_value=0.0, value=0.0)
  with colp2:
    unit_type = st.text_input("मापक इकाई (KG/PCS/MTR)", value="PCS")
  with colp3:
    assessed_goods_value = st.number_input("आंकलित कुल करयोग्य मूल्य (₹)", min_value=0.0, value=0.0)
  colp4, colp5 = st.columns(2)
  with colp4:
    tax_rate_pct = st.number_input("कर की दर (%)", min_value=0.0, value=18.0)
  with colp5:
    tax_type = st.selectbox("कर प्रकार", ["CGST+SGST", "IGST"])
  bill_tax_amount = round(assessed_goods_value * tax_rate_pct / 100, 2)
  penalty_amount = round(bill_tax_amount * 2, 2)
  st.info(f"आंकलित कर राशि ≈ ₹{bill_tax_amount:,.2f} — धारा 129(1)(a) अंतर्गत पेनल्टी (200% कर) = कुल ₹{penalty_amount:,.2f}")

else:  # 129(1)(b) — स्वामी अस्वीकृत
  colp1, colp2, colp3 = st.columns(3)
  with colp1:
    market_rate_per_unit = st.number_input("प्रचलित बाजार भाव (₹ प्रति इकाई)", min_value=0.0, value=0.0)
  with colp2:
    unit_type = st.text_input("मापक इकाई (KG/PCS/MTR)", value="PCS")
  with colp3:
    assessed_goods_value = st.number_input("आंकलित कुल मूल्य (₹)", min_value=0.0, value=0.0)
  penalty_amount = st.number_input(
      "धारा 129(1)(b) अंतर्गत अंतिम पेनल्टी राशि (₹)* — कृपया दर्ज करें", min_value=0.0, value=0.0
  )

# ============================================================
# STEP 11 — अन्य कमियाँ
# ============================================================
st.header("1️⃣1️⃣ अन्य कमियाँ")
has_other_defects = st.checkbox("क्या अन्य कमियाँ हैं?", value=True)
other_defects_list = []
base_case_ctx = {
    "goods_description": goods_description, "vehicle_no": vehicle_no,
    "case_details_para": case_details_para, "docs_stage1_para": docs_stage1_para,
    "docs_after_verification_para": docs_after_verification_para,
    "physical_verification_para": physical_verification_para,
    "place_of_loading": place_of_loading, "place_of_delivery": place_of_delivery,
}
if has_other_defects:
  defect_count = st.number_input("कमियों की संख्या", min_value=1, max_value=10,
                                  value=st.session_state.defect_count, step=1, key="defect_count_input")
  st.session_state.defect_count = defect_count
  for i in range(int(defect_count)):
    st.markdown(f"**कमी #{i+1}:**")
    dcol1, dcol2 = st.columns([1, 2])
    with dcol1:
      d_head = st.text_input(f"कमी #{i+1} का शीर्षक", key=f"dh_{i}", value="")
      if st.button(f"✨ #{i+1} पूरा पैरा AI से लिखें", key=f"expand_{i}"):
        with st.spinner("पैरा तैयार किया जा रहा है..."):
          try:
            st.session_state[f"dd_{i}"] = expand_defect_paragraph(d_head, base_case_ctx)
          except Exception as e:
            st.error(f"त्रुटि: {e}")
    with dcol2:
      d_detail = st.text_area(f"कमी #{i+1} का पूरा विवरण", key=f"dd_{i}")
    other_defects_list.append({"heading": d_head, "details": d_detail})

for idx, d in enumerate(other_defects_list):
  d["label"] = str(idx + 1)

# ============================================================
# STEP 12 — समीक्षा करें (न्यायिक निर्णय + AI गैप-सुझाव)
# ============================================================
st.header("1️⃣2️⃣ समीक्षा करें")
if st.button("🔍 समीक्षा करें (न्यायिक निर्णय व सम्भावित कमियाँ खोजें)", type="primary"):
  defects_text_for_ai = " ".join([f"{d['heading']}: {d['details']}" for d in other_defects_list])
  with st.spinner("प्रासंगिक न्यायिक निर्णय खोजे जा रहे हैं..."):
    try:
      st.session_state["judgments_cache"] = fetch_judicial_precedents(defects_text_for_ai or goods_description, count=3)
    except Exception as e:
      st.error(f"त्रुटि: {e}")
      st.session_state["judgments_cache"] = []

  penalty_calc_data = {
      "penalty_clause": penalty_clause, "claimant": claimant, "ownership_accepted": ownership_accepted,
      "bill_accepted": bill_accepted, "goods_description": goods_description,
      "bill_taxable_value": bill_taxable_value, "tax_rate_pct": tax_rate_pct, "tax_type": tax_type,
      "bill_tax_amount": bill_tax_amount, "market_rate_per_unit": market_rate_per_unit,
      "unit_type": unit_type, "assessed_goods_value": assessed_goods_value, "penalty_amount": penalty_amount,
  }
  with st.spinner("टैक्स/पेनल्टी पैरा तैयार किया जा रहा है..."):
    try:
      st.session_state["tax_penalty_cache"] = compose_tax_penalty_para(penalty_calc_data)
    except Exception as e:
      st.error(f"त्रुटि: {e}")
      st.session_state["tax_penalty_cache"] = ""

  ownership_valuation_data = {
      "claimant": claimant, "ownership_accepted": ownership_accepted,
      "ownership_rejection_reason": ownership_rejection_reason,
      "bill_accepted": bill_accepted, "bill_rejection_reason": bill_rejection_reason,
      "penalty_clause": penalty_clause,
  }
  with st.spinner("स्वामित्व/मूल्यांकन पैरा तैयार किया जा रहा है..."):
    try:
      st.session_state["ownership_valuation_cache"] = compose_ownership_valuation_para(ownership_valuation_data)
    except Exception as e:
      st.error(f"त्रुटि: {e}")
      st.session_state["ownership_valuation_cache"] = ""

  draft_context = {
      "firm_or_driver_name": firm_or_driver_name, "case_details_para": case_details_para,
      "docs_stage1_para": docs_stage1_para, "physical_verification_para": physical_verification_para,
      "initial_ground_order_para": initial_ground_order_para,
      "docs_after_verification_para": docs_after_verification_para,
      "other_defects_list": other_defects_list, "transporter_name": transporter_name,
      "vehicle_owner_name": vehicle_owner_name, "penalty_calc_data": penalty_calc_data,
  }
  with st.spinner("नोटिस की समीक्षा कर संभावित कमियाँ खोजी जा रही हैं..."):
    try:
      st.session_state.notice_gaps = suggest_notice_gaps(draft_context)
    except Exception:
      st.session_state.notice_gaps = []

  st.session_state.gap_paragraphs = {}
  st.session_state.gap_selected = {}
  for gi, gap in enumerate(st.session_state.notice_gaps):
    try:
      st.session_state.gap_paragraphs[gi] = expand_gap_to_paragraph(gap, base_case_ctx)
    except Exception:
      st.session_state.gap_paragraphs[gi] = gap
    st.session_state.gap_selected[gi] = False
  st.session_state.show_review = True

# ============================================================
# समीक्षा परिणाम दिखाएँ (यदि उपलब्ध)
# ============================================================
extra_defects_from_gaps = []
if st.session_state.show_review and st.session_state.notice_gaps:
  st.subheader("⚠️ AI समीक्षा में मिलीं संभावित कमियाँ")
  st.caption("प्रत्येक कमी के लिए तय करें कि नोटिस में जोड़नी है या नहीं — जोड़ने से पहले पैरा संपादित भी कर सकते हैं।")
  for gi, gap in enumerate(st.session_state.notice_gaps):
    gcol1, gcol2 = st.columns([1, 3])
    with gcol1:
      st.markdown(f"**सुझाव:** {gap}")
      add_this = st.checkbox("नोटिस में जोड़ें", key=f"gap_add_{gi}")
    with gcol2:
      gap_para = st.text_area("पैरा (जोड़ने से पहले संपादित करें):", key=f"gap_para_{gi}", height=80,
                               value=st.session_state.gap_paragraphs.get(gi, gap))
    if add_this:
      extra_defects_from_gaps.append({"heading": gap[:60], "details": gap_para})

# combine original defects + accepted gap-defects, renumber
final_defects_list = list(other_defects_list) + extra_defects_from_gaps
for idx, d in enumerate(final_defects_list):
  d["label"] = str(idx + 1)

# ============================================================
# अंतिम नोटिस जनरेट करें
# ============================================================
if st.session_state.show_review:
  st.header("📄 अंतिम नोटिस")
  if st.button("📄 अंतिम नोटिस जनरेट व डाउनलोड करें", type="primary"):
    context = {
        "party_block_type": "multi" if multi_party else "single",
        "firm_or_driver_name": firm_or_driver_name, "gstin": gstin, "tmp_id": tmp_id,
        "case_id": case_id, "vehicle_no": vehicle_no, "notice_no": notice_no,
        "notice_date": notice_date.strftime("%d/%m/%Y"),
        "transporter_name": transporter_name, "vehicle_owner_name": vehicle_owner_name,
        "case_details_para": case_details_para,
        "docs_stage1_present": docs_stage1_present, "docs_stage1_heading": docs_stage1_heading,
        "docs_stage1_para": docs_stage1_para,
        "initial_ground_order_para": initial_ground_order_para,
        "physical_verification_para": physical_verification_para,
        "later_docs_present": docs_after_present, "later_docs_para": docs_after_verification_para,
        "other_defects_list": final_defects_list,
        "judgments": st.session_state.get("judgments_cache", []),
        "ownership_valuation_para": st.session_state.get("ownership_valuation_cache", ""),
        "tax_penalty_para": st.session_state.get("tax_penalty_cache", ""),
        "officer_name": officer_name or "[अधिकारी का नाम]",
        "officer_designation": officer_designation, "officer_unit": officer_unit,
    }
    template_file = "mov_07_template.docx"
    if not os.path.exists(template_file):
      st.error(f"टेम्प्लेट फ़ाइल '{template_file}' नहीं मिली!")
    else:
      try:
        doc = DocxTemplate(template_file)
        doc.render(context)
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        st.download_button(
            label="📄 MOV-07 नोटिस (.docx) डाउनलोड करें", data=buffer,
            file_name=f"FORM_GST_MOV_07_{vehicle_no.replace(' ', '_')}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
        )
      except Exception as e:
        st.error(f"Word फ़ाइल जनरेट करने में त्रुटि: {e}")