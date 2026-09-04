# Document Ingestion Pipeline

## Overview

Getting a document from a user's upload into a searchable, chunked, embedded form ready for retrieval involves several distinct stages running in sequence, each of which can independently fail without corrupting the document's state in a way that would confuse a retry. A document's status field moves through a small number of named states as it progresses — uploaded, being scanned, being embedded, and finally ready — and any document that has not reached the ready state is invisible to retrieval, so a document that is still mid-pipeline can never leak partial or unfinished content into a chat answer.

## Upload and Presigned URLs

A document upload does not send file bytes through Aether's own API servers at all. Instead, the client requests a presigned upload URL scoped to a specific object key and a maximum allowed size, and uploads the file bytes directly to object storage using that presigned URL. This keeps large file transfers off the request path of Aether's own application servers entirely, and the presigned URL's built-in expiry and size limit mean a client cannot use it to upload something other than what it was actually issued for. Once the upload completes, a message describing the new document is placed on an outbox for asynchronous processing rather than being processed synchronously as part of the original upload request, since scanning and embedding a large document can take meaningfully longer than a client should be expected to wait on a single HTTP request.

## Malware Scanning

The very first thing that happens to an uploaded document once processing picks it up is a full malware scan against the actual file bytes, using a real antivirus engine rather than a filename or content-type heuristic. A document that fails this scan is immediately marked as rejected and never proceeds to chunking or embedding under any circumstances, regardless of what its declared file type claimed to be. This scan happens before anything else in the pipeline specifically so that no later stage — parsing, chunking, or embedding — ever operates on bytes that haven't already been confirmed clean.

## Chunking Strategy

Once a document passes scanning, its text is extracted and split into chunks using the same chunking logic described in Aether's retrieval documentation: a roughly five-hundred-and-twelve-token target with an eight-hundred-token hard ceiling, sentence-aware splitting that never breaks a sentence in half, and a ten to fifteen percent overlap carried forward within a section but not across a heading boundary. This chunking step is where the shape of everything downstream gets decided: a document that produces only one chunk because it was too short will only ever be retrievable as a single, undifferentiated unit, while a longer, well- structured document with multiple headings will produce multiple independently retrievable chunks, each carrying its own section path recording which heading was active when that particular chunk closed.

## Embedding

Every chunk produced during chunking is embedded using whichever embedding provider is currently configured for the deployment. If a real embedding provider's API key is configured, chunks receive genuinely semantic embeddings capturing meaning rather than exact wording. If no real provider key is configured, Aether falls back to a local, deterministic, hash-based embedding scheme that is fully functional as a stand-in for exercising the rest of the pipeline honestly, but is not actually semantic: two chunks discussing the same concept in different words will not necessarily produce similar hash-based embeddings the way a real trained embedding model's output would. This fallback exists so that development and evaluation environments without a configured provider key still produce a real, working, honestly-labeled embedding rather than a fake placeholder that silently pretends to be semantic when it is not. Every embedding is stamped with which model produced it and a version number, so that chunks embedded by different models or different versions of the same model are never silently compared against each other as if they were on the same scale.

## Outbox Dispatch and Retry

The asynchronous processing that picks up a newly uploaded document off the outbox runs as a separate worker process, polling for undispatched messages rather than receiving a live push notification for each one. If processing a document fails partway through — a transient network issue reaching object storage, for instance — the outbox message is not discarded; it becomes eligible for another delivery attempt on the worker's next poll. Each message tracks how many delivery attempts have already been made, and a message that exhausts its configured maximum number of attempts without ever succeeding is moved into a dead-letter state rather than being retried forever, so that one permanently broken document cannot silently consume worker capacity indefinitely while never actually completing.

## Failure Handling

A document whose processing fails outright, as opposed to merely being retried, is marked with a status reflecting that failure rather than being left stuck in an ambiguous intermediate state. This distinction matters operationally: a dashboard watching outbox age and dead-letter depth can tell the difference between documents that are still legitimately in progress and documents that have definitively failed and need attention, rather than lumping every non-ready document into one undifferentiated bucket that gives no signal about whether anything is actually wrong.

## Supported File Types and Size Limits

A presigned upload URL is issued with an explicit maximum size baked in at the moment it is created, and object storage itself enforces that ceiling on the upload, rather than Aether trusting a client-declared size and checking it only after the fact. The declared content type accompanying an upload is used to select which mime type gets recorded against the document, but it is never trusted as a substitute for the malware scan that always runs against the actual bytes regardless of what type the upload claimed to be — a file that claims to be a harmless document but whose real bytes are something else entirely still goes through the exact same scanning step as every other upload before anything is trusted about its content.

## Document Status Visibility

A document's status is visible to the workspace that owns it at every stage of the pipeline, not only once processing finishes, so a client can show a real, honest "still processing" state to a user rather than the document simply appearing to not exist until it becomes ready. Listing a workspace's documents includes documents that have failed processing as well as ones still in progress, each carrying their own real status value, rather than filtering failed or in-progress documents out of the list entirely — a document that failed to ingest is still a real fact about the workspace worth surfacing, not something to quietly hide from view.
