# IDT ebooks

Folder-preserving import of the approved ISKCON Desire Tree ebook manifest.

## Source list

The original idt.txt contains 6,402 nonblank URL lines. Every URL appears twice. `urls.txt` contains all **3,201 unique URLs: 3,200 PDFs and 1 ZIP**, in original order. Files with the same content at different paths are still preserved separately.

The importer validates the exact count and canonical SHA-256 before downloading:

```
5552cb245abf5542235f9d7a12c4e25c51ce95f60f3c7178486cb04b0c0ad033
```

## Running and progress

Committing `urls.txt` starts **Actions > Download ebooks**. Use **Run workflow** to resume or retry. `mirror.py` launches `downloader.py`. No custom token or secret is needed.

Four concurrent downloads, three attempts for transient errors, commits and pushes every 12 files. Successfully imported files are skipped on rerun only after checking their size and SHA-256. Response length and PDF/ZIP signatures are checked.

All files retain the full source `pdf/...` folder hierarchy, original case, punctuation, and decoded names. Some source directory names end in spaces; those are deliberately preserved and may require special handling on Windows. The Action runs on Linux.

See `reports/status.json` and the import-status Action artifact for successful, failed, oversized, and pending URLs. The Action reports failure if any files remain incomplete rather than claiming success.

Normal GitHub storage blocks files at 100 MiB. Such files are explicitly marked oversized; paid LFS is not enabled automatically. The downloader also stops conservatively before an 8 GiB payload budget, on low runner disk space, or after approximately four hours, preserving completed batches. A larger total or oversized files require a separate storage decision. No recurring schedule is installed.

Source: https://ebooks.iskcondesiretree.com/

Original copyrights and permissions remain with their respective rights holders; this repository grants no new license to third-party books.
