from enum import StrEnum


class EncounterStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    VOID = "void"


class Measurement(StrEnum):
    HEART_RATE = "heart_rate"
    RESPIRATORY_RATE = "respiratory_rate"
    TEMPERATURE = "temperature"
    SYSTOLIC_BLOOD_PRESSURE = "systolic_blood_pressure"
    DIASTOLIC_BLOOD_PRESSURE = "diastolic_blood_pressure"
    OXYGEN_SATURATION = "oxygen_saturation"
    BODY_WEIGHT = "body_weight"
    BODY_HEIGHT = "body_height"
    BLOOD_GLUCOSE = "blood_glucose"


class Unit(StrEnum):
    BEATS_PER_MINUTE = "bpm"
    BREATHS_PER_MINUTE = "breaths/min"
    CELSIUS = "degC"
    FAHRENHEIT = "degF"
    MILLIMETERS_OF_MERCURY = "mmHg"
    PERCENT = "%"
    KILOGRAMS = "kg"
    CENTIMETERS = "cm"
    MILLIMOLES_PER_LITER = "mmol/L"


class ObservationSource(StrEnum):
    CLINICIAN = "clinician"
    DEVICE = "device"
    PATIENT_REPORTED = "patient_reported"
    IMPORTED = "imported"


class EvidenceKind(StrEnum):
    PRESENTING_COMPLAINT = "presenting_complaint"
    VITAL_OBSERVATION = "vital_observation"


class ReviewDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    OVERRIDDEN = "overridden"


class QueueEventType(StrEnum):
    ENTERED = "entered"
    CLAIMED = "claimed"
    RELEASED = "released"
    REMOVED = "removed"


class ToolExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditEntityType(StrEnum):
    PATIENT = "patient"
    ENCOUNTER = "encounter"
    COMPLAINT = "complaint"
    VITAL_OBSERVATION = "vital_observation"
    ASSESSMENT_VERSION = "assessment_version"
    CLINICIAN_REVIEW = "clinician_review"
    QUEUE_EVENT = "queue_event"
    TOOL_EXECUTION = "tool_execution"
