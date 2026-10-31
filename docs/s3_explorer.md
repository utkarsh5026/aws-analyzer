---
title: S3 Explorer Guide
description: "How to click through Amazon S3 buckets and folders like a file explorer in a SageMaker notebook with aws-analyzer's s3_explorer.py, and see what's inside each file as you click it."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/s3.svg" alt="" width="32" height="32"> Amazon S3 <code>s3_explorer.py</code></p>

# Click through S3 like the folders on your laptop

`S3Explorer()` turns a notebook cell into a file explorer. Your buckets and folders are on the left; click a file and the right shows what's inside it: a table's first rows, a PDF page by page, a Word file with its pictures, an archive's contents. Nothing to type, nothing to remember.
{ .lede }

<ul class="pills">
  <li>One install, boto3 + ipywidgets</li>
  <li>Read-only: never changes your data</li>
  <li>Opens folders of millions of files</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use a bucket called `acme-ml-data`, and the screenshots are the real explorer in JupyterLab on a demo bucket. For reports you run from code (costs, duplicates, lifecycle rules, deleted files), see the [S3 guide](s3.md).
{ .muted }

![S3Explorer in a notebook, animated: it starts on the list of four buckets; the pointer opens acme-ml-data, then curated, features and churn, and clicks train.parquet, whose preview appears on the right with its row, column and row-group counts and first rows; then it clicks acme-ml-data in the path at the top, opens docs and clicks a Word model card, which appears on the right laid out with its title, headings and bullet points; last it opens training, turns on Include subfolders and clicks the .tar chip, and the list shows each model.tar.gz below with the training run it came from](images/explorer-tour-light.webp#only-light){ width="984" height="578" loading=lazy }
![S3Explorer in a notebook, animated: it starts on the list of four buckets; the pointer opens acme-ml-data, then curated, features and churn, and clicks train.parquet, whose preview appears on the right with its row, column and row-group counts and first rows; then it clicks acme-ml-data in the path at the top, opens docs and clicks a Word model card, which appears on the right laid out with its title, headings and bullet points; last it opens training, turns on Include subfolders and clicks the .tar chip, and the list shows each model.tar.gz below with the training run it came from](images/explorer-tour-dark.webp#only-dark){ width="984" height="578" loading=lazy }
/// caption
`S3Explorer()`: from your buckets to a Parquet file's first rows, a Word document, and every model file below a folder, one click at a time.
///

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell (`[all]` adds what the previews use: pandas, pyarrow, the PDF and Excel readers):

    ```bash
    %pip install "aws-analyzer[all]"
    ```

2. **Open the explorer.** It uses the notebook's IAM role, so there's nothing to configure.

    ```python
    from aws_analyzer import S3Explorer

    S3Explorer()                                           # start from your buckets
    S3Explorer("s3://acme-ml-data/curated/")               # or in a folder; S3 console links work too
    S3Explorer("s3://acme-ml-data/curated/features/churn/train.parquet")   # a file's folder, with the file shown
    ```

</div>

??? more "No internet in the notebook (VPC-only mode)?"

    On a computer that has internet, download the package and copy it to a bucket the notebook can read (`pip download aws-analyzer --no-deps -d wheels`, then `aws s3 cp --recursive wheels/ s3://acme-ml-data/tools/wheels/`), and install it from there:

    ```bash
    !aws s3 cp --recursive s3://acme-ml-data/tools/wheels/ wheels/
    %pip install --no-index --find-links wheels aws-analyzer
    ```

??? more "Another profile or region, a fixed height, bigger zips"

    ```python
    from aws_analyzer import S3Analyzer

    S3Explorer("s3://acme-ml-data/", profile="dev")   # another AWS profile (or region=)
    S3Explorer(core=S3Analyzer(region="eu-west-1"))   # an S3Analyzer or S3View you already have
    S3Explorer(height=720, page_size=200)            # 720px panes (else they fill the window), 200 rows a page
    S3Explorer(zip_max_size="2GB")                   # zip folders up to 2 GB (100 MB by default)
    S3Explorer(downloads="~/scratch/s3")             # save downloads and zips there (default: s3-downloads)
    ```

    It needs boto3 and ipywidgets, both on SageMaker already. Without ipywidgets, or outside Jupyter, each folder prints as a table instead (see [without the window](#text)). A preview that needs a package that isn't installed says which one.

## Start here { #start }

Three things to try in your first minute. Nothing you click changes anything in S3.
{ .gist }

<div class="quick" markdown>

-   **Look inside a file**

    Open a bucket, open its folders, click a file. Its first rows, pages or contents show on the right.

    [Look inside a file](#files)

-   **Find every file of one type**

    Type `.parquet` in the search box and turn on **Include subfolders**.

    [Find files](#search)

-   **Take some files home**

    Tick them, click **⬇ Download selected**, then download the zip from JupyterLab's file browser.

    [Download some files](#select)

-   **Load a file in your code**

    Click it, then **🐍 Use in Python**: the lines for a notebook cell, written for its format.

    [Use it in Python](#use-in-python)

</div>

## Find your way around { #browse }

The folder you're in is on the left, whatever you clicked on the right. Click folders to go down, the path at the top to go back up.
{ .gist }

![S3Explorer: a toolbar with back, forward, up and refresh buttons, the path s3:// All buckets / acme-ml-data / curated / features / churn, and edit and settings buttons; on the left a search box, All, Folders and Files buttons and Include subfolders over three parquet files with sizes and ages, train.parquet highlighted; on the right Preview, Details, Download, Open in new tab and S3 console buttons over the preview of train.parquet with row, column and row-group counts and the first rows](images/explorer-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer: a toolbar with back, forward, up and refresh buttons, the path s3:// All buckets / acme-ml-data / curated / features / churn, and edit and settings buttons; on the left a search box, All, Folders and Files buttons and Include subfolders over three parquet files with sizes and ages, train.parquet highlighted; on the right Preview, Details, Download, Open in new tab and S3 console buttons over the preview of train.parquet with row, column and row-group counts and the first rows](images/explorer-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
The folder on the left, the file you clicked on the right. One click on the path at the bottom selects it, ready to copy.
///

- **Open a folder** by clicking it. **←** **→** go back and forward, **↑** goes up a level, and each part of the path at the top opens that folder; **All buckets**, its first part, goes back to your buckets.
- **Go to a path** with **✎**: paste an `s3://` path, `bucket/folder`, an S3 console link or an object URL, and press Enter. A path to a file opens its folder with the file shown. **✕** closes the box without going anywhere.
- **Find files** with the search box over the list: a name, a file type such as `.csv`, or only the folders. [More below](#search).
- **Sort** by clicking **Name**, **Size** or **Modified**, and again for the other way. Sizes and dates sort biggest and newest first, folders stay on top, and names sort the way people count: `part-2` before `part-10`.
- **Page through** a long list with **«** **‹** **›** **»** under it. The list shows 100 rows at a time, and the bar says which ones, of how many: `2,401–2,500 of 3,000`.
- **The bar at the bottom** counts the folders and files listed and adds up their size. On the right it shows the path of the folder you're in, or of the file you clicked; one click selects it, ready to copy.

### Go back to where you were { #recent }

The explorer remembers the folders you open, so your list of buckets starts with them: **↩** picks up where you left off.
{ .gist }

![S3Explorer on the list of buckets: over the search box a # recent row of chips, ↩ acme-ml-data/…/churn/ first, then acme-ml-data/training/, acme-ml-data/…/dt=2025-10-10/ and acme-ml-data/docs/; under it the buckets](images/explorer-recent-light.webp#only-light){ width="984" height="579" loading=lazy }
![S3Explorer on the list of buckets: over the search box a # recent row of chips, ↩ acme-ml-data/…/churn/ first, then acme-ml-data/training/, acme-ml-data/…/dt=2025-10-10/ and acme-ml-data/docs/; under it the buckets](images/explorer-recent-dark.webp#only-dark){ width="984" height="579" loading=lazy }
/// caption
`S3Explorer()`: the folder you were in last, then the ones you open most. Point at a chip for its whole path.
///

The **Recent** row shows over your list of buckets, and over any folder while the **✎** path box is open. After ↩, the chips are the folders you open most *and* most lately: a folder you open every day beats one you opened thirty times in March. One click opens it. `x.recent` lists them as `s3://` paths.

??? more "What it keeps, where, and how to turn it off"

    Only the folders' paths and when you opened them, per AWS account, and each folder's count of files and their size when you were last there ([below](#since)), never what's in a file. They're in one file, `~/.cache/aws-analyzer/history/places.sqlite`, which `clean_up()` keeps. Each explorer keeps its newest 500 visits.

    `x.forget_recent("s3://bucket/folder/")` forgets one folder, and `x.forget_recent()` every folder. `S3Explorer(history=False)` remembers nothing, `history="path/to/file.sqlite"` keeps them in another file, and the environment variable `AWS_ANALYZER_HISTORY=off` turns it off for every explorer.

### What's new since your last visit { #since }

Open a folder you've been in before, and its overview says what arrived since: how many files are new or changed, and ● marks them in the list.
{ .gist }

![S3Explorer on the churn folder: holdout.parquet and train.parquet marked with a green ● just now in the Modified column; on the right the cards Files 4, Size 1.3 GB, Newest file just now and New since 2d ago 2, then the note: Since your last visit 2d ago: 2 files are new or changed (73.2 KB), marked ● in the list; the folder went from 3 to 4 files](images/explorer-changes-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer on the churn folder: holdout.parquet and train.parquet marked with a green ● just now in the Modified column; on the right the cards Files 4, Size 1.3 GB, Newest file just now and New since 2d ago 2, then the note: Since your last visit 2d ago: 2 files are new or changed (73.2 KB), marked ● in the list; the folder went from 3 to 4 files](images/explorer-changes-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
A folder you were in two days ago: one file replaced, one added.
///

It's read from the listing the explorer makes anyway, so it costs nothing extra. A visit is everything within an hour, so the comparison is with your visit before this one, not with a minute ago.

??? more "How it decides what's new"

    A file is new or changed when S3 says it was modified after your last visit. S3 doesn't tell a new file from a replaced one, so they're counted together. The count of files is compared with your last visit's only when both listings were whole: in a folder bigger than what's listed, it says "listed so far". With **Include subfolders**, the files below are marked the same way, but the counts aren't compared.

??? more "What the icons mean, and what the right shows before you click a file"

    Each file's icon says what it is: 📊 tables (CSV, Parquet, Avro...), 📗 Excel, 📕 PDF, 📘 Word, 📙 PowerPoint, 📋 JSON, YAML and other config, 🖼️ pictures, 🎵 audio, 🎬 video, 📦 archives, 🧠 models, 🔢 NumPy arrays and 📓 notebooks. ❄ marks a file in [Glacier](#archived).

    Until you click a file, the right shows what the folder holds, from the listing it already has: how many folders and files, their size, the newest file, how many are archived, and the file types by size. These numbers cover the files at this level; **Include subfolders** [counts everything below](#search), and **📊 What's in here** [adds it all up](#folders).

## Find files { #search }

Type part of a name, or a file type like `.csv`, to narrow the list. It searches the whole folder, not only the rows on screen.
{ .gist }

A big folder is [listed in the background](#big) as soon as it opens, and typing doesn't ask S3 for anything.

![S3Explorer in the training folder with .tar in the search box and Include subfolders on: chips for .pt (600 files), .tar (13, lit) and .json (12); the list shows each model.tar.gz with the training run folder it's in, its size and age; on the right 38 folders and 625 files below, 1.0 TB, and the file types below](images/explorer-search-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer in the training folder with .tar in the search box and Include subfolders on: chips for .pt (600 files), .tar (13, lit) and .json (12); the list shows each model.tar.gz with the training run folder it's in, its size and age; on the right 38 folders and 625 files below, 1.0 TB, and the file types below](images/explorer-search-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
`.tar` with **Include subfolders**: every model artifact below `training/`, each under the run it came from.
///

- **Search by name** by typing part of it, in any case. Several words must all be in the name (`churn train`), and `*` `?` make a pattern for the whole name (`part-0*`).
- **Search by file type** by typing it with its dot: `.csv`, `.parquet`, or `.csv .json` for either. A type also finds its compressed files (`.csv` finds `data.csv.gz`) and its other spelling (`.jpg` finds `.jpeg`); `*.csv` works too. Folders have no type, so they drop out.
- **Click a type** instead: under the search box is a chip for each file type here, with how many files have it, the most common first (**+N more** shows the rest). A click writes `.csv` into the search box, so you can see and change what it did; click another to add it, or the same one again to take it out.
- **Show only folders, or only files** with **All**, **Folders** and **Files**. Each says how many of its kind match the search. It stays on as you open other folders, like the sort, so **Folders** walks down a tree of folders without the files.
- **Include subfolders** lists everything below the folder, not only its first level, with the folder each file is in under its name. Then the search, the chips and the sort cover all of it: every `.parquet` file in a dataset's partitions, the biggest or newest files anywhere below (sort by **Size** or **Modified**), or a folder by name. It lists 10,000 files at a time (**Load more from S3** lists the next), one S3 request per 1,000, and the right shows what they add up to.
- **✕** in the search box clears it. Opening another folder starts with an empty search and only its first level.

When nothing matches, the note under the list says why and offers the fix: **Search the subfolders too** when it's only at this level, or **Show the 25 files** when **Folders** hides them. With a search, the bar at the bottom also adds up how many match and how big they are.

## Look inside a file { #files }

Click a file and the right shows what's inside it: a table's first rows, a PDF's pages, a Word file with its pictures, an archive's contents.
{ .gist }

Every [file type `s3.py` reads](s3.md#file-types) works here. The buttons above it do the rest:

| Button | What it does |
| --- | --- |
| **👁️ Preview** | What's inside the file (the [`preview`](s3.md#files) report) |
| **🏷️ Details** | Its size, dates, storage class, encryption, version, metadata and tags (the `head` report) |
| **📖 Read all** | For a PDF, Word or PowerPoint file: the whole document, page by page or slide by slide (the [`document`](s3.md#documents) report) |
| **📄 Text** | For a PDF: its words laid out to read, with headings, paragraphs and lists, [50 pages at a time](#pdf) |
| **🐍 Use in Python** | The lines that load the file in a notebook cell, written for its format ([see below](#use-in-python)) |
| **🕘 Versions** | In a versioned bucket: every version of the file, when each was written and its size (the [`history`](s3.md#deleted) report) |
| **⬇ Download** | Saves a copy in `s3-downloads`, next to the notebook (the [`download`](s3.md#download) report; **⚙** changes the folder) |
| **↗ Open in new tab** | Opens the file in a new browser tab: PDFs, pictures, sound, video and text show there, even when they were stored as a generic type, and other files download to your computer. Its link works for an hour: right-click it to copy the link for someone without AWS access, or run `x.ui.link(path)` to see it written out |
| **↗ S3 console** | Opens the file's page in the AWS console in a new tab: its properties, permissions and versions, where you can change what the explorer only reads. The console asks you to sign in with your own AWS account; `x.console_url` is the link |
| **▾ Expand all** | For a JSON file: opens every object and array in the tree at once, instead of a click on each. It stays on for the next JSON files you open, until you click it again. Long strings stay collapsed to their start; click one to read it |
| **‹** **›** | The file before and after it in the list, as it's sorted and searched, shown the same way (its preview, details or code), without going back to the list |
| **✕** | Closes the file and shows the folder again |

Clicking through files doesn't wait: previews load in the background, and files you clicked past are skipped.

![S3Explorer in the docs folder: a PDF, a Word file and a PowerPoint deck on the left, the Word model card highlighted; on the right its word, paragraph, heading, table and picture counts, title and author, and the document laid out with its headings and lists](images/explorer-docx-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer in the docs folder: a PDF, a Word file and a PowerPoint deck on the left, the Word model card highlighted; on the right its word, paragraph, heading, table and picture counts, title and author, and the document laid out with its headings and lists](images/explorer-docx-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
A Word file's first paragraphs; **📖 Read all** shows the whole document, pictures in place.
///

### Use it in Python { #use-in-python }

**🐍 Use in Python** writes the lines that load the file in your next cell: one click selects them, ready to copy.
{ .gist }

The code reads the file for its format: `pd.read_csv` for a CSV, `pd.read_parquet` for Parquet, `json.load` for JSON, `Image.open` for a picture. It uses `S3Analyzer().open(...)`, so it needs only boto3 and pandas, signs in the way the explorer does, and undoes `.gz` compression as it reads. Tick several files of one table format and **🐍 Use in Python** in the bar under the list writes the loop that puts them in one DataFrame.

![S3Explorer with train.parquet highlighted and 🐍 Use in Python pressed; on the right the title Use train.parquet in Python over a code block that imports pandas, makes an S3Analyzer, opens the file's s3:// path and reads it with pd.read_parquet, then notes that Parquet is read whole into memory and the path for other tools](images/explorer-python-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer with train.parquet highlighted and 🐍 Use in Python pressed; on the right the title Use train.parquet in Python over a code block that imports pandas, makes an S3Analyzer, opens the file's s3:// path and reads it with pd.read_parquet, then notes that Parquet is read whole into memory and the path for other tools](images/explorer-python-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
**🐍 Use in Python** on a Parquet file: copy the lines, run them, and the table is a DataFrame.
///

??? more "Pickles, other tools, and the paths from code"

    A pickle comes with a warning: loading one runs whatever code its writer put in it. Formats without a reader in pandas get the reader they need (`fastavro`, `pypdf`, `numpy`, `zipfile`), and an unknown format gets the file's bytes. To use another tool, the note under the code gives the `s3://` path; `pd.read_csv("s3://...")` reads it directly when `s3fs` is installed. From code, `x.selected` is the file shown and `x.picked` the files ticked.

### Read a PDF, as it looks or as text { #pdf }

**📖 Read all** shows a PDF's pages as they look; **📄 Text** shows its words laid out to read, without the headers and page numbers on every page.
{ .gist }

Click a page to see it as big as the notebook, with **‹** **›** to step through. **📄 Text** is the quickest way to read a long report, or to check what a knowledge base or a model will get from a PDF.

![S3Explorer in the docs folder with the data retention policy PDF highlighted and 📄 Text pressed; on the right its page and word counts, title and author, then the policy laid out to read under a thin Page 1 line: its title, a paragraph, a heading and a bulleted list](images/explorer-pdf-text-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer in the docs folder with the data retention policy PDF highlighted and 📄 Text pressed; on the right its page and word counts, title and author, then the policy laid out to read under a thin Page 1 line: its title, a paragraph, a heading and a bulleted list](images/explorer-pdf-text-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
**📄 Text** on a two-page PDF: the words to read, without the header and page numbers repeated on every page.
///

??? more "Pages 20 at a time, scans, and what it needs"

    **📖 Read all** shows 20 pages at a time, each page's text folded underneath; buttons under the last page show the 20 before and after. **📄 Text** shows 50 pages at a time. A page without text (a scan) says so, and **📖 Read all** shows it as a picture. A PDF only places lines of text on a page, so the layout is worked out: lists drawn as shapes read as a paragraph, and a table reads as one line per row (see [the S3 guide](s3.md#documents)).

    **📄 Text** needs `pypdf`, and drawing the pages also needs `pypdfium2` and `pillow` (`%pip install pypdf pypdfium2 pillow`); without them, the button says what to install. Word and PowerPoint files need nothing extra.

### Files in Glacier { #archived }

Files marked ❄ are in Glacier and can't be read until they're restored. Opening one shows the restore command, ready to copy.
{ .gist }

A restore takes 3 to 5 hours for GLACIER and up to 12 for DEEP_ARCHIVE, and keeps the file readable for 7 days:

```bash
aws s3api restore-object --bucket acme-ml-data --key raw/2021/events.csv \
    --restore-request Days=7
```

The explorer never runs it: a restore costs a retrieval fee, so that's your call.

## Folders and buckets { #folders }

The buttons above the right pane change with where you are: a summary of a folder, a bucket's settings, or every bucket side by side.
{ .gist }

| Where | Button | What it shows |
| --- | --- | --- |
| In a folder | **📊 What's in here** | Every file below this folder, not only this level: sizes, file types, the biggest files and folders, the monthly cost, and findings (the [`summary`](s3.md#summary) report). It lists everything below, so a big folder takes a while; a progress bar shows how far it's got |
| In a folder | **⬇ Download .zip** | Everything below this folder as one `.zip` ([see below](#zip)) |
| Anywhere | **↗ S3 console** | Opens this folder, or your list of buckets, in the AWS console in a new tab, to change what the explorer only reads (`x.console_url` is the link) |
| In a folder | **More ▾** | A second row of reports on the folder ([see below](#more)). It stays open as you move, until **Fewer ▴** |
| At a bucket's top level | **🛡️ Bucket settings** | Versioning, encryption, public access, lifecycle rules and the policy in plain English, and what's risky (the [`bucket_info`](s3.md#buckets) report) |
| On your buckets | **🪣 Every bucket** | Each bucket's size, monthly cost and security warnings, side by side (the [`overview`](s3.md#buckets) report) |

![S3Explorer on the list of buckets: four buckets on the left with how long ago each was created; on the right the Every bucket and S3 console buttons over the Every bucket report, with cards for buckets, objects, total size, estimated monthly cost and buckets with warnings, and a table of each bucket's region, objects, size, cost, versioning and encryption](images/explorer-buckets-light.webp#only-light){ width="984" height="579" loading=lazy }
![S3Explorer on the list of buckets: four buckets on the left with how long ago each was created; on the right the Every bucket and S3 console buttons over the Every bucket report, with cards for buckets, objects, total size, estimated monthly cost and buckets with warnings, and a table of each bucket's region, objects, size, cost, versioning and encryption](images/explorer-buckets-dark.webp#only-dark){ width="984" height="579" loading=lazy }
/// caption
`S3Explorer()` starts from your buckets; **🪣 Every bucket** compares their size, cost and security settings.
///

### More reports on a folder { #more }

**More ▾** answers the questions you have while you browse: what's taking the space, what landed recently, what's a copy.
{ .gist }

| Button | What it shows |
| --- | --- |
| **🐘 Largest files** | The 20 biggest files below this folder (the [`largest`](s3.md#browse) report) |
| **🆕 Newest files**, **🕰️ Oldest files** | The 20 changed most recently, or left alone the longest |
| **🔍 Inside the files** | What's inside each file: a table's rows and columns, and whether the tables here agree on them, a PDF's pages, a picture's size (the [`file_details`](s3.md#file-details) report) |
| **📑 Copies** | Files stored more than once, matched by size and ETag so no file is read, and what the copies cost (the [`duplicates`](s3.md#duplicates) report) |
| **🧩 Unfinished uploads** | Uploads that never finished: billed, but in no listing (the [`uploads`](s3.md#costs) report) |
| **🕘 Old versions**, **🗑️ Deleted files** | In a versioned bucket only: what old versions cost, and the files you can still bring back, with the call that does it (the [`versions`](s3.md#costs) and [`deleted`](s3.md#deleted) reports) |

Each one lists everything below the folder, so a big one takes a while; a progress bar shows how far it's got. Nothing changes anything in S3.

![S3Explorer in the acme-ml-data bucket with More ▾ open: a second row of buttons for Largest files, Newest files, Oldest files, Inside the files, Copies and Unfinished uploads; Largest files is pressed and the right shows a table of the 20 biggest files below the bucket with their sizes, ages and storage classes](images/explorer-more-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer in the acme-ml-data bucket with More ▾ open: a second row of buttons for Largest files, Newest files, Oldest files, Inside the files, Copies and Unfinished uploads; Largest files is pressed and the right shows a table of the 20 biggest files below the bucket with their sizes, ages and storage classes](images/explorer-more-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
**More ▾** → **🐘 Largest files**: where a bucket's space goes, without leaving the explorer.
///

### Download a folder as a .zip { #zip }

**⬇ Download .zip** packs the folder you're in into one `.zip` next to the notebook. Right-click it in JupyterLab's file browser and choose **Download**.
{ .gist }

First it checks, like [`download_zip()`](s3.md#download), that the folder is within 100 MB and 10,000 files and that there's room; if a check fails, it says which and writes nothing.

??? more "Bigger limits, another folder, and cleaning up"

    **⚙** at the top right changes the limits and the folder downloads and zips go to (Enter in a box saves, like **Save**). They last until the kernel restarts. To start with others:

    ```python
    x = S3Explorer(zip_max_size="2GB")   # folders up to 2 GB
    x.zip_max_files = 50_000             # and up to 50,000 files
    x.downloads = "~/zips"               # where ⬇ Download and the zips save; made with the first one
    ```

    The folder is the same one `S3View`'s [`download()`](s3.md#downloads) uses, so `x.ui.downloads()` in a cell lists what you've downloaded and how much disk it takes, and `x.ui.clean_downloads()` deletes it. Files in Glacier are left out of a zip, and the report says how many.

### Download some files { #select }

Tick a few files, or a search's worth, and download them as one zip.
{ .gist }

<div class="steps" markdown>

1. **Tick the files.** Point at a row and a checkbox shows on its left; click it. Once something is ticked, every row shows its checkbox. The one in the header ticks everything listed, so a search and one click selects, say, every `.csv` in a dataset's partitions (with [**Include subfolders**](#search)). You can tick folders too: they go in with everything below them.
2. **Click ⬇ Download selected** in the bar under the list. The bar also says how many are ticked and how big they are; **Clear** unticks them.
3. **Check the name and click ⬇ Download.** The right shows what goes in the zip and whether it's within the limits, and suggests a name from the folder and how many files there are, such as `churn-12-files.zip`. Change it if you like (Enter downloads too). The explorer never replaces a file: when that name is taken, it suggests `churn-12-files-2.zip`.

</div>

![S3Explorer in a folder of 30 compressed JSON event files, five of them ticked and tinted; the bar under the list says 5 selected and their size, with Clear and Download selected; on the right, Download 5 files as one .zip, with cards for the files, their size and the limit, a Save as box holding dt=2026-10-10-5-files.zip, Download and Cancel buttons, and a table of what goes in the zip](images/explorer-zip-light.webp#only-light){ width="984" height="581" loading=lazy }
![S3Explorer in a folder of 30 compressed JSON event files, five of them ticked and tinted; the bar under the list says 5 selected and their size, with Clear and Download selected; on the right, Download 5 files as one .zip, with cards for the files, their size and the limit, a Save as box holding dt=2026-10-10-5-files.zip, Download and Cancel buttons, and a table of what goes in the zip](images/explorer-zip-dark.webp#only-dark){ width="984" height="581" loading=lazy }
/// caption
Five files ticked, one `.zip` with a name that says what's in it.
///

??? more "How the zip is laid out, and doing it from code"

    The zip holds the files as they're laid out below the folder they share, so files from different folders keep their paths and never clash. It goes through the same checks as a folder's (size, file count, disk space and read access), and [**⚙**](#zip) sets the same limits. Opening another folder starts a new selection; the search, the sort and **↻** keep it. From code, `x.picked` lists what's ticked, and `x.ui.download_zip(x.picked)` zips it. **🐍 Use in Python** in the bar writes the code that [loads what's ticked](#use-in-python) instead.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: big folders, driving the explorer from code, permissions, troubleshooting and every argument.</p>

## Big folders { #big }

The explorer lists one level at a time, never the whole bucket, so a folder opens in a moment even in a bucket of billions of files.
{ .gist }

- S3 lists a folder 1,000 entries per request. The explorer shows the first 1,000 at once, then lists the rest in the background, up to 10,000 entries (about a second per few thousand). Meanwhile you can click, sort and search; each page that comes in updates the list, the counts, the type chips and your search, and the bar at the bottom says **Listing…** with a spinner. Opening another folder stops it, and coming back carries on from where it stopped.
- So the search, the sort and the counts cover the whole folder: a file that's the 2,500th in S3's order is found by typing part of its name, and **Size** puts the biggest file in the folder on top, not the biggest of the first 1,000.
- The list shows 100 rows a page (`page_size=`); **«** **‹** **›** **»** under it move between pages, and the bar says which rows these are, of how many.
- A folder of more than 10,000 entries stops there: the counts end in **+**, and the right says so. **Load more from S3**, at the end of the list, lists the next 10,000. **Look up** finds a name past what's listed: type the start of it in the search box (`2025-09-` for a date partition), and it asks S3 for the names that start with it. To list more at once, set `x.nav.list_limit = 50_000` before opening the folder.
- **Include subfolders** lists the files below the folder the same way, in the background, 10,000 at a time; **Load more from S3** lists the next 10,000.
- Opening a file by its path (`S3Explorer("s3://…/img_02750.jpg")`) finds it however far down the folder it is.
- Folders you've opened are kept, so going back is instant. **↻** lists the folder again, to pick up files added or removed since.

Listing is cheap: S3 charges $0.005 per 1,000 list requests (us-east-1 list price), so the 10 requests for 10,000 entries cost $0.00005. Only **📊 What's in here** and **⬇ Download .zip** read everything below a folder; the list stops at 10,000 entries until you ask for more.

## From code { #code }

The explorer is an object you can drive from other cells. Keep it in a variable:

```python
x = S3Explorer("s3://acme-ml-data/")
x.open("s3://acme-ml-data/raw/events/")   # the toolbar from code: open, back, forward, up, refresh
x.back(); x.forward(); x.up(); x.refresh()
x.filter(".parquet", subfolders=True)      # the search box and its buttons: every Parquet file below this folder
x.filter(kind="folders")                   # only the folders here; x.filter() shows everything again
x.picked                                   # what's ticked in the list, as s3:// paths
x.location                                 # the folder you're in, such as 's3://acme-ml-data/raw/' ('' on your buckets)
x.selected                                 # the file shown on the right, or ''
x.console_url                              # the AWS console's page for that file, else the folder
x.focus.chips()                            # what you're looking at, as a row of chips: the folder, the file, the search
x.ui.summary(x.location)                   # any S3View report about where you are, in its own cell
```

`x.ui` is an ordinary [`S3View`](s3.md) on the same connection, so its reports appear under the cell you run them in and stay in the saved notebook. The explorer doesn't: it lives in the running kernel, so a reopened notebook needs its cell run again. `x.nav` is the `S3Navigator` behind the list.

### Without the window { #text }

Outside Jupyter, without `ipywidgets`, or with `S3Explorer(mode="text")`, the explorer prints each folder as a table: what it holds, the file types, and its folders and files with their sizes and ages. `open()`, `back()`, `forward()` and `up()` print the next one, `filter()` prints what matches, and opening a file prints its preview.

### The navigation as data { #python }

`S3Navigator` is the explorer without the UI: the same listing, history and cache, returning data instead of drawing it. A folder it can't list has the reason in `folder.error` instead of raising.

```python
from aws_analyzer import S3Navigator

nav = S3Navigator()                                  # or S3Navigator(S3Analyzer(profile="dev"))
folder = nav.open("s3://acme-ml-data/curated/")      # Folder: entries, more, error
[(e.name, e.kind, e.size) for e in folder.entries]   # Entry: kind ('bucket', 'folder', 'file'), key, size, modified, storage_class
nav.more()                                           # the next 1,000 entries, when folder.more is True
nav.list_rest()                                      # the rest of the folder, up to 10,000 entries (nav.list_limit)
nav.lookup("2025-09-")                               # adds the names that start with it; returns how many were new
below = nav.below()                                  # everything below: every file and the folders between (10,000 at a time)
nav.more(below=True)                                 # the next 10,000, when below.more is True
nav.back(); nav.forward(); nav.up(); nav.refresh()
```

The functions behind it don't call AWS, so they work on anything:

```python
from aws_analyzer.s3_explorer import parse_location, breadcrumbs, filter_entries, folder_stats

parse_location("https://us-east-1.console.aws.amazon.com/s3/buckets/acme-ml-data?prefix=curated/features/")
# ('acme-ml-data', 'curated/features/')
parse_location("https://acme-ml-data.s3.us-east-1.amazonaws.com/reports/q3.pdf")
# ('acme-ml-data', 'reports/q3.pdf')
breadcrumbs("s3://acme-ml-data/curated/")
# [('All buckets', ''), ('acme-ml-data', 's3://acme-ml-data/'), ('curated', 's3://acme-ml-data/curated/')]
folder_stats(folder.entries)                         # FolderStats: counts, size, newest and oldest file, types by size
filter_entries(below.entries, "churn .parquet", kind="files")   # what the search box and Files show
```

The others are `parent_uri`, `folder_uri`, `sort_entries` (folders first, `part-2` before `part-10`; `"path"` for a tree), `parse_filter` (what a search asks for: its words, patterns and file types), `count_types` (the type chips: files and bytes per type), `entry_icon` and `explain_list_error`.

## Permissions { #permissions }

The explorer only reads. A folder or bucket the notebook's role can't list shows a note that names the missing permission instead of an error, and you can still type the path of one you can read. This policy covers browsing and opening files:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BrowseS3",
      "Effect": "Allow",
      "Action": ["s3:ListAllMyBuckets", "s3:ListBucket", "s3:GetObject", "s3:GetObjectTagging"],
      "Resource": "*"
    }
  ]
}
```

| Permission | Used by |
| --- | --- |
| `s3:ListAllMyBuckets` | The list of buckets you start from. Without it, open a bucket by its path |
| `s3:ListBucket` | Listing folders, **Include subfolders**, **Look up**, **📊 What's in here** and **⬇ Download .zip** |
| `s3:GetObject` | Opening files: **👁️ Preview**, **🏷️ Details**, **📖 Read all**, **📄 Text**, **⬇ Download** and **⬇ Download .zip**, and the links **↗ Open in new tab** opens |
| `s3:GetObjectTagging` | The tags in **🏷️ Details**; without it, the rest of **🏷️ Details** still shows |
| `s3:GetBucketVersioning` | Whether a bucket keeps old versions, for **🕘 Versions**, **🕘 Old versions** and **🗑️ Deleted files**; without it, those buttons don't show |
| `s3:ListBucketVersions` | **🕘 Versions**, **🕘 Old versions** and **🗑️ Deleted files** |
| `s3:ListBucketMultipartUploads` | **🧩 Unfinished uploads** |
| `kms:Decrypt` on the key | Files encrypted with SSE-KMS |

**🛡️ Bucket settings** and **🪣 Every bucket** also read each bucket's settings, and its size from CloudWatch; the [S3 guide's policy](s3.md#permissions) covers them and every other S3 report. To scope the policy down, give `s3:ListBucket` and `s3:GetObject` your buckets' ARNs, such as `arn:aws:s3:::acme-ml-data` and `arn:aws:s3:::acme-ml-data/*`, in a statement of their own: `s3:ListAllMyBuckets` only works with `"Resource": "*"`.

## Troubleshooting { #troubleshooting }

??? question "The cell shows a table instead of the explorer, or “Error displaying widget”"

    Clicking needs ipywidgets in the kernel and its extension in JupyterLab. On SageMaker both are there. Elsewhere, run `%pip install ipywidgets`, restart the kernel and reload the browser tab. An explorer from before the notebook was reopened can't come back (it lived in the old kernel): run its cell again.

??? question "“Your AWS role isn't allowed to list this”"

    The notebook's role is missing `s3:ListBucket` on that bucket (or `s3:ListAllMyBuckets`, for the list of buckets). Ask for it (see [permissions](#permissions)), or press **✎** and type the path of a folder you can read.

??? question "A file I know is there isn't in the list"

    If the bar at the bottom says **Listing…**, the explorer is still listing the folder: the file shows up when its page comes in. A folder of more than 10,000 entries is listed 10,000 at a time, and the search covers what's listed: type the start of the name in the search box and click **Look up**, or click **Load more from S3** at the end of the list. If the file is in a subfolder, turn on **Include subfolders**; if **Folders** or **Files** is lit, click **All**. If the file was added after the folder was opened, press **↻**. Names in S3 are case-sensitive.

??? question "A file marked ❄ won't open"

    It's in GLACIER or DEEP_ARCHIVE. The note on the right has the `aws s3api restore-object` command that makes it readable ([files in Glacier](#archived)). Run it, or ask whoever manages the bucket; when the restore has finished, press **↻** and click the file again.

??? question "A preview says a package is missing"

    Install it in the notebook's kernel, for example `%pip install openpyxl` for Excel, and click the file again.

??? question "A click on a row did nothing"

    The explorer ignores a click that comes within a third of a second after the list changed, because it was aimed at the rows that were there before (a double click on a folder would otherwise open whatever took its place). Click again.

??? question "The zip wasn't made"

    The report says which check failed. If the folder or the files you ticked are over a limit, **⚙** raises it (or untick some files). If the disk is full, free some space (the [SageMaker guide's `disk()`](sagemaker_env.md#disk) shows what fills it) or zip a smaller folder.

## Reference { #reference }

`S3Explorer(uri="", core=None, *, profile=None, region=None, height=560, page_size=100, zip_max_size="100MB", downloads=None, mode="auto", progress="auto", history=True)` opens the explorer and returns it.

<div class="ref" markdown>

| Argument | What it does |
| --- | --- |
| `uri` | Where to start: a folder, a file (its folder opens with the file shown), `bucket/prefix`, an S3 console link or an object URL. Left out: your buckets |
| `core` | An `S3Analyzer` or `S3View` to use; without one, one is made from `profile` and `region` |
| `profile`, `region` | The AWS profile and region |
| `height` | The height of the two panes, in pixels |
| `page_size` | Rows on each page of the list; **«** **‹** **›** **»** under it move between pages |
| `zip_max_size` | The biggest folder **⬇ Download .zip** packs; **⚙** changes it |
| `downloads` | The folder **⬇ Download** and the zips save into: `s3-downloads`, next to the notebook, unless set; **⚙** changes it |
| `mode` | `"auto"`: the clickable explorer in Jupyter, a text listing elsewhere. `"widgets"` or `"text"` to choose |
| `progress` | Progress bars for long reports: `"auto"`, `"plain"` or `"off"`, as for `S3View` |
| `history` | Remember the folders you open, for the [Recent row](#recent): `True`, `False`, or a file to keep them in |

| Command | What it does |
| --- | --- |
| `open(uri="")` | Goes to a folder, or shows a file in its folder; `open()` goes to your buckets |
| `back()`, `forward()` | **←** and **→** |
| `up()` | **↑**: the folder above |
| `refresh()` | **↻**: lists this folder again |
| `recent` | The folders you've opened in this account, as `s3://` paths, the [Recent row](#recent)'s order |
| `forget_recent(uri="")` | Forgets one folder in the Recent row, or every one |
| `filter(text="", kind="all", subfolders=False)` | The search box and its buttons: names or file types (`".csv .json"`), `kind="folders"` or `"files"`, and everything below with `subfolders=True`. It searches the whole folder, up to its first 10,000 entries, and in a bigger one also asks S3 for the names that start with `text`. `filter()` shows everything again |
| `location` | The folder you're in, or `""` on your buckets |
| `selected` | The file shown on the right, or `""` |
| `console_url` | The AWS console's page for the file shown, else the folder you're in, else your buckets: what **↗ S3 console** opens |
| `ui` | An `S3View` for your own cells: `x.ui.summary(x.location)` |
| `nav` | The `S3Navigator` behind the list |
| `picked` | The files and folders ticked in the list, as `s3://` paths: `x.ui.download_zip(x.picked)` |
| `focus` | What you're looking at, as data: the folder (what it holds, as far as it's listed), the file shown, the search and what's ticked. `x.focus.chips()` reads it as a row of chips, `x.focus.value("file")` one part |
| `zip_max_size`, `zip_max_files` | The **⬇ Download .zip** and **⬇ Download selected** limits, which **⚙** edits |
| `downloads` | Where **⬇ Download** and the zips save (the `S3Analyzer`'s downloads folder), which **⚙** edits; `zip_folder` is its old name |

</div>
