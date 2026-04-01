export type JobStatus = "queued" | "processing" | "completed" | "failed";
export type PipelineStepStatus = "pending" | "running" | "completed" | "failed";
export type ClinicalRelevance = "clinical" | "functional" | "administrative" | "unknown";

export interface PipelineStep {
  key: string;
  label: string;
  status: PipelineStepStatus;
  detail: string | null;
}

export interface Citation {
  page: number;
  matching_text: string;
}

export interface FieldCitation {
  field: string;
  citations: Citation[];
}

export interface PageExtraction {
  page_number: number;
  patient_name: string | null;
  document_title: string | null;
  document_type: string | null;
  document_date: string | null;
  author: string | null;
  clinical_relevance: ClinicalRelevance;
  starts_new_document: boolean;
  starts_new_patient: boolean;
  boundary_hint: string | null;
  visible_text: string;
  citations: FieldCitation[];
}

export interface DocumentSummary {
  id: string;
  title: string;
  patient_name: string | null;
  document_type: string | null;
  document_date: string | null;
  author: string | null;
  page_numbers: number[];
  page_range: string;
  classification: ClinicalRelevance;
  include_in_output: boolean;
  capture_status: string;
  summary: string;
}

export interface ExportArtifact {
  filename: string;
  content_type: string;
  ready: boolean;
  size_bytes: number | null;
}

export interface ExtractionJobSummary {
  id: string;
  filename: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  page_count: number;
  patient_count: number;
  document_count: number;
  capture_certification: string | null;
  pipeline: PipelineStep[];
  export_artifact: ExportArtifact;
  error: string | null;
}

export interface ExtractionJobDetail extends ExtractionJobSummary {
  source_available: boolean;
  pages: PageExtraction[];
  documents: DocumentSummary[];
}

export interface CreateJobResponse {
  job: ExtractionJobSummary;
}

export interface JobListResponse {
  jobs: ExtractionJobSummary[];
}
