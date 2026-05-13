import logging
import os
import asyncio
from datetime import datetime
from typing import Dict, Any
import hl7

logger = logging.getLogger(__name__)

def fhir_to_adt_a08(target_system: str, fhir_payload: Dict[str, Any], target_id: str, trace_id: str) -> str:
    """Map FHIR Patient to HL7 v2 ADT^A08."""
    dt_str = datetime.now().strftime("%Y%m%d%H%M%S")
    msh_trace = trace_id[:15] if trace_id else "NW-1"
    
    msh = f"MSH|^~\\&|NODEWIRE|AOT|{target_system.upper()}|HOSPITAL|{dt_str}||ADT^A08|{msh_trace}|P|2.4"
    evn = f"EVN|A08|{dt_str}"
    
    names = fhir_payload.get("name", [])
    name = names[0] if names else {}
    family = name.get("family", "")
    given = "^".join(name.get("given", []))
    
    dob = fhir_payload.get("birthDate", "").replace("-", "")
    
    # Map FHIR gender to HL7 Administrative Sex
    fhir_gender = fhir_payload.get("gender", "unknown").lower()
    gender_map = {"male": "M", "female": "F", "other": "O", "unknown": "U"}
    hl7_sex = gender_map.get(fhir_gender, "U")
    
    pid = f"PID|1||{target_id}^^^^MR||{family}^{given}||{dob}|{hl7_sex}"
    
    return f"{msh}\r{evn}\r{pid}\r"

async def send_adt_a08(
    target_system: str, 
    fhir_payload: Dict[str, Any], 
    target_id: str, 
    trace_id: str
) -> str:
    """
    Translates a FHIR Patient payload into an HL7 v2 ADT^A08 message
    and transmits it via MLLP over a TCP socket to the hospital Interface Engine.
    """
    logger.info("Building ADT^A08 message for %s (ID: %s)", target_system, target_id)
    
    hl7_msg_str = fhir_to_adt_a08(target_system, fhir_payload, target_id, trace_id)
    logger.debug("HL7 Message Generated: %s", hl7_msg_str.replace('\r', '<CR>'))
    
    host = os.getenv("HL7_TARGET_HOST")
    port_str = os.getenv("HL7_TARGET_PORT", "2575")
    
    if not host:
        logger.warning("HL7_TARGET_HOST not set. Simulating MLLP transmission for ADT^A08...")
        return "AA"
        
    port = int(port_str)
    logger.info("Connecting to HL7 Interface Engine at %s:%d via MLLP", host, port)
    
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        
        # Wrap message in MLLP tags (VT ... FS CR)
        VT = b'\x0b'
        FS = b'\x1c'
        CR = b'\x0d'
        
        mllp_payload = VT + hl7_msg_str.encode("utf-8") + FS + CR
        writer.write(mllp_payload)
        await writer.drain()
        
        # Read ACK
        ack_data = await asyncio.wait_for(reader.readuntil(FS + CR), timeout=10.0)
        writer.close()
        await writer.wait_closed()
        
        # Parse ACK
        ack_str = ack_data.decode("utf-8").strip('\x0b\x1c\x0d')
        logger.debug("HL7 ACK Received: %s", ack_str.replace('\r', '<CR>'))
        
        # Use python-hl7 to parse
        h = hl7.parse(ack_str)
        # MSA segment is usually the second segment
        msa = h.segment("MSA")
        ack_code = str(msa[1])
        
        return ack_code
        
    except asyncio.TimeoutError:
        logger.error("Timeout connecting/reading from HL7 target %s:%d", host, port)
        return "AE"
    except Exception as e:
        logger.error("MLLP connection failed: %s", e)
        return "AE"
