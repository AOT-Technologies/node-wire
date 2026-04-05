import asyncio
import base64
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Add 'src' to sys.path so we can import core components if running from root
sys.path.append(os.path.join(os.getcwd(), "src"))

from connectors.fhir_epic.logic import FhirEpicConnector
from connectors.fhir_epic.schema import (
    FhirPatientReadInput,
    FhirEncounterSearchInput,
    FhirDocumentReferenceCreateInput
)
from runtime import SecretProvider

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("scenario_post_visit")

class EnvSecretProvider(SecretProvider):
    """Simple provider that could pull from env or a dict."""
    def __init__(self, secrets: Dict[str, str]):
        self.secrets = secrets
    def get_secret(self, key: str) -> str:
        return self.secrets.get(key, "")

async def run_scenario():
    """
    Real-world Scenario: Post-Consultation Clinical Note Upload.
    
    Workflow:
    1. Search for a Patient by demographics.
    2. Find the most recent 'finished' Encounter for that patient.
    3. Upload a clinical note (DocumentReference) tied to that Encounter.
    """
    
    # Load env vars for secrets
    from dotenv import load_dotenv
    load_dotenv()

    secrets = {
        "epic_fhir_base_url": os.getenv("EPIC_FHIR_BASE_URL", "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"),
        "epic_token_url": os.getenv("EPIC_TOKEN_URL", "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"),
        "epic_client_id": os.getenv("EPIC_CLIENT_ID", "CLIENT_ID_HERE"),
        "epic_kid": os.getenv("EPIC_KID", "KID_HERE"),
        "epic_private_key": os.getenv("EPIC_PRIVATE_KEY", "PRIVATE_KEY_HERE")
    }
    
    if "CLIENT_ID_HERE" in secrets.values():
        logger.warning("Using placeholder secrets. Ensure .env is populated with real Epic Sandbox credentials.")

    connector = FhirEpicConnector(secret_provider=EnvSecretProvider(secrets))
    trace_id = "scenario-trace-123"

    print("\n=== STEP 1: Patient Discovery ===")
    patient_search_params = {"family": "Smith", "given": "Jason", "birthdate": "1985-01-01"}
    logger.info(f"Searching for patient: {patient_search_params}")
    
    try:
        patient_result = await connector.internal_execute(
            FhirPatientReadInput(action="read_patient", search_params=patient_search_params),
            trace_id=trace_id,
        )
        patient_id = patient_result.resource.get("id")
        logger.info(f"Found Patient ID: {patient_id}")
    except Exception as e:
        logger.error(f"Patient search failed: {e}")
        return

    print("\n=== STEP 2: Encounter Identification ===")
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    encounter_params = {
        "patient": patient_id,
        "status": "finished",
        "date": today
    }
    logger.info(f"Finding encounter for patient {patient_id} on {today}")
    
    try:
        enc_result = await connector.internal_execute(
            FhirEncounterSearchInput(action="search_encounter", search_params=encounter_params),
            trace_id=trace_id,
        )

        if not enc_result.resources:
            logger.warning("No encounters found for this patient today. Falling back to most recent.")
            enc_result = await connector.internal_execute(
                FhirEncounterSearchInput(
                    action="search_encounter",
                    search_params={"patient": patient_id, "status": "finished"},
                ),
                trace_id=trace_id,
            )
        
        if not enc_result.resources:
            logger.error("No finished encounters found for this patient.")
            return
            
        encounter_id = enc_result.resources[0].get("id")
        logger.info(f"Selected Encounter ID: {encounter_id}")
    except Exception as e:
        logger.error(f"Encounter search failed: {e}")
        return

    print("\n=== STEP 3: Clinical Note Upload ===")
    note_content = "Patient Jason Smith presented for follow-up. Vital signs stable. Plan: Continue current medication."
    encoded_note = base64.b64encode(note_content.encode('utf-8')).decode('utf-8')
    
    doc_input = FhirDocumentReferenceCreateInput(
        action="create_document_reference",
        identifier=[{"system": "urn:oid:1.2.3", "value": f"NOTE-{datetime.now().timestamp()}"}],
        status="current",
        type={"coding": [{"system": "http://loinc.org", "code": "11506-3", "display": "Progress Note"}]},
        subject=f"Patient/{patient_id}",
        data=encoded_note,
        content_type="text/plain",
        author=[{"reference": "Practitioner/ebmR9M-H9f6.dummy", "display": "Dr. Automated"}], 
        description="Automated Post-Consultation Note Demo",
        context={
            "encounter": [{"reference": f"Encounter/{encounter_id}"}]
        }
    )
    
    logger.info(f"Uploading clinical note for Encounter {encounter_id}")
    try:
        doc_result = await connector.internal_execute(doc_input, trace_id=trace_id)
        logger.info(f"SUCCESS! Created DocumentReference: {doc_result.resource_id}")
        print(f"\nWorkflow Complete. Resource Created: {doc_result.resource_id}")
    except Exception as e:
        logger.error(f"Document upload failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_scenario())
