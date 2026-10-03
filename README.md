# Philippine House Republic Acts linkage — research snapshot v0.1

**Status: preliminary; not a validated enactment ground truth.** This is a reproducible snapshot of observed official House Republic Acts listing records and proposed links to the C8–C20 House bills master. It is intended for audit and subsequent integration, not as a replacement for the master.

Source: https://www.congress.gov.ph/legislative-documents/republic-acts . Source HTML was captured through an authorized browser session and parsed locally. This release includes derived CSVs and a source hash manifest; original HTML snapshots and the 149,474-row input master are not included in this compact package.

## Manifest-reported counts

- 5,724 raw listing cards; 5,691 distinct RA identifiers; 5,689 RA/HB link rows.
- 5,681 uniquely matched links; 8 unmatched or held links; 6 laws with metadata conflicts.
- The separately generated enriched master preserved 149,474 rows, but is deliberately **not** included in this preliminary Git package (169 MB and pending audit).

## Files

- `data/01_RA_ARCHIVE_RECORDS.csv`: source listing cards, source URL, page filename, page and record SHA-256.
- `data/02_REPUBLIC_ACTS.csv`: deduplicated RA-number table; metadata conflict flag.
- `data/03_RA_HOUSE_BILL_LINKS.csv`: candidate RA-to-HB joins with status and evidence fields; CAP/gender placeholders must not be interpreted as classifications.
- `data/05_LINKAGE_AND_METADATA_AUDIT.csv`: held, missing-HB, and conflict cases.
- `data/06_VALIDATION_MANIFEST.json`: output hashes and original snapshot hashes; see provenance caveat below.

## Important known limitations

1. **Parser contamination:** RA06636's extracted `ra_title` includes unrelated joint-resolution text. Other title fields require a systematic card-boundary audit before substantive title analysis or clean publication. Do not infer policy classifications from these extracted RA titles yet.
2. Archive pagination was deduplicated: original source page 1 repeated page 0; derived validated page 0 maps to source page 0, and derived pages 1–11 map to actual website pages 2–12. The manifest's source URLs for derived pages 1–11 are therefore **not the actual original website page numbers**. Refer to `docs/SOURCE_PAGE_CROSSWALK.csv` for the corrected provenance mapping. The original source HTML is not included here.
3. Saved-page coverage is not independently certified as complete website coverage. Page 13 had no parseable records and page 12 was partial; further completeness checking is appropriate.
4. Unmatched House bill **does not mean not enacted**. No enactment dates are inferred. Preserve prior enactment fields and adjudicate discrepancies before merging.
5. The 8 held/unmatched link rows and 6 metadata-conflict laws require review; no title-based joins or arbitrary disambiguation.
6. Output hashes in the manifest describe the original generated CSVs, which are included unchanged here.

## Reproduction

The collector and `ra_pipeline.py` scripts used locally are not included in this package because their exact source bytes were not supplied in this conversation. Add and review those original scripts from the working `ra_extension` directory before describing this as a fully executable replication package. Do not commit browser profiles, session data, or credentials. Run the original `ra_pipeline.py check --html-dir ./ra_archive_validated` against locally preserved HTML; use the original House master for linkage with `(congress, normalized bill_no)` and `measure_id` as stable bill key.

## Citation

Philippine House of Representatives, Republic Acts listing, research extraction snapshot dated 2026-10-03; derived linkage prepared for the House Bills C8–C20 project. Please cite the official House archive as the primary source and this repository version for derived data.
