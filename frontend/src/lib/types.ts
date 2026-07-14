export type JobStatus = "queued" | "processing" | "completed" | "failed" | "cancelled";
export type PipelineStepStatus = "pending" | "running" | "completed" | "failed";
export type ClinicalRelevance = "clinical" | "functional" | "administrative" | "unknown";
export type PageRole = "document_start" | "document_body" | "separator" | "index_only" | "cover" | "other";
export type SummaryKind = "clinical" | "imaging" | "pathology" | "functional" | "administrative" | "unknown";

export interface PipelineStep {
  key: string;
  label: string;
  status: PipelineStepStatus;
  detail: string | null;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
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
  patient_dob: string | null;
  patient_identifier: string | null;
  patient_key: string | null;
  mentioned_patient_names: string[];
  document_title: string | null;
  document_type: string | null;
  document_bucket: SummaryKind;
  document_date: string | null;
  author: string | null;
  author_role: string | null;
  page_role: PageRole;
  clinical_relevance: ClinicalRelevance;
  starts_new_document: boolean;
  starts_new_patient: boolean;
  boundary_hint: string | null;
  document_boundary_reason: string | null;
  patient_boundary_reason: string | null;
  accession_number: string | null;
  exam_title: string | null;
  radiologist_name: string | null;
  narrative_report_available: boolean | null;
  visible_text: string;
  citations: FieldCitation[];
}

export interface DocumentSummary {
  id: string;
  title: string;
  patient_name: string | null;
  patient_dob: string | null;
  patient_identifier: string | null;
  patient_key: string | null;
  patient_group_id: string | null;
  mentioned_patient_names: string[];
  document_type: string | null;
  summary_kind: SummaryKind;
  document_date: string | null;
  author: string | null;
  author_role: string | null;
  page_numbers: number[];
  page_range: string;
  classification: ClinicalRelevance;
  include_in_output: boolean;
  capture_status: string;
  accession_number: string | null;
  exam_title: string | null;
  radiologist_name: string | null;
  narrative_report_available: boolean | null;
  summary: string;
}

export interface PatientHeader {
  to_name: string | null;
  claim_number: string | null;
  from_name: string | null;
  age_dob: string | null;
  review_date: string | null;
  occupation: string | null;
  claimant: string | null;
  diagnosis_dod: string | null;
}

export interface SummaryParagraph {
  text: string;
  page_start: number;
  page_end: number;
  document_id: string | null;
  document_type: string | null;
  document_number: number;
  is_lab: boolean;
  /** Coverage placeholder for pages no real document claimed (admin/blank/unreadable). Optional: absent on jobs stored before this field existed. */
  is_placeholder?: boolean;
}

export interface PatientSummary {
  id: string;
  name: string | null;
  header: PatientHeader;
  summary: string;
  summary_paragraphs: SummaryParagraph[];
  page_start: number;
  page_end: number;
  opinion: string;
}

export interface ExportArtifact {
  filename: string;
  content_type: string;
  ready: boolean;
  size_bytes: number | null;
}

export interface CostStageBreakdown {
  stage: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  cost_usd: number;
}

export interface CostModelBreakdown {
  provider: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  cost_usd: number;
}

export interface CostSummary {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  cost_usd: number;
  by_stage: CostStageBreakdown[];
  by_model: CostModelBreakdown[];
}

export interface ExtractionJobSummary {
  id: string;
  source_file_id?: string | null;
  filename: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  processing_started_at?: string | null;
  page_count: number;
  patient_count: number;
  document_count: number;
  capture_certification: string | null;
  pipeline: PipelineStep[];
  export_artifact: ExportArtifact;
  cost_summary?: CostSummary;
  error: string | null;
}

export interface ExtractionJobDetail extends ExtractionJobSummary {
  source_available: boolean;
  pages: PageExtraction[];
  documents: DocumentSummary[];
  patients: PatientSummary[];
}

export interface CreateJobResponse {
  job: ExtractionJobSummary;
}

export interface JobListResponse {
  jobs: ExtractionJobSummary[];
}

export type VaultPreviewKind = "pdf" | "image" | "audio" | "video" | "doc" | "download";
export type VaultFileSourceKind = "upload" | "job_source" | "legacy_import";

export interface VaultFolderSummary {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface VaultFileSummary {
  id: string;
  name: string;
  folder_id: string | null;
  content_type: string;
  size_bytes: number;
  extension: string | null;
  preview_kind: VaultPreviewKind;
  can_extract: boolean;
  source_kind: VaultFileSourceKind;
  checksum_sha256: string;
  linked_job_id: string | null;
  linked_job_status: JobStatus | null;
  created_at: string;
  updated_at: string;
}

export interface VaultBrowseResponse {
  current_folder: VaultFolderSummary | null;
  breadcrumbs: VaultFolderSummary[];
  folders: VaultFolderSummary[];
  files: VaultFileSummary[];
}

export interface VaultRecentResponse {
  files: VaultFileSummary[];
}

export interface VaultUploadResponse {
  files: VaultFileSummary[];
}

export interface VaultFolderResponse {
  folder: VaultFolderSummary;
}

export interface VaultFileResponse {
  file: VaultFileSummary;
}

export interface LlmRunFileMeta {
  exists: boolean;
  size_bytes: number;
  updated_at: number | null;
}

export interface LlmRunSummary {
  id: string;
  updated_at: number;
  files: {
    parse: LlmRunFileMeta;
    summarize: LlmRunFileMeta;
  };
}

export interface LlmRunListResponse {
  runs: LlmRunSummary[];
}

export type LlmRunStage = "parse" | "summarize";

export interface LlmRunEntriesResponse {
  run_id: string;
  stage: LlmRunStage;
  entries: Record<string, unknown>[];
  total: number;
}
