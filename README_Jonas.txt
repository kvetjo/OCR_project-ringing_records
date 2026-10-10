Project: Digitisation of ringing and sightings data - converting image to JSONL/CSV
This is quick README created by Jonas. There is also complex documentation created by AI in folder Docs


Quick start - how to run the program: 
-------------------------------------

--> first thing to do is installing dependecies:    pip install -r requirements.txt

--> main workflow is split into two python files ("make_OCR_batch.py" and "get_OCR_results.py") which need to be run asynchronously
--> before you run whatever you need to setup the config file ("config.yaml")
--> there are some tools which are not explicitly part of the workflow but can be used after the workflow is done (convert to CSV, etc..)



CREATING BATCHES (uploading them to AI provider):

---make_OCR_batch.py---

1. prepare scaned sheets - it can be saved in "Cloudflare" OR "Local folder"  (BE AWARE: 10 GB is max free storage in Cloudflare)
    --> you have to keep specific structure which is "batch_X/page_Y"   (important naming convention)
2. prepare AI prompt and include it into "prompts.yaml"  OR  use already prepared one from "prompts.yaml"
    --> you select this prepared prompt in "config.yaml" file in "active_prompt_key" section
3. setup config.yaml properly (how to setup config is explained bellow OR read comments in config.yaml)
4. run the script "make_OCR_batch.py" (python make_OCR_batch.py)  
    --> no configuration inside this file needed, run it as it is
5. script will create batch for selected AI provider of selected images - and it will give you "Batch ID" !!
    --> now you need to wait until the processing is DONE (usually 12 to 24 hours, sometimes faster - depends)
    --> copy "Batch ID" or check this file: "state/processed_batches.json" where the Batch ID should be saved - it is important to have batch_id



what to setup in config.yaml for make_OCR_batch.py: 

  active_provider: "gemini"                     # openai | gemini | ollama | ollama-ssh
  model_name: "gemini-3.1-pro-preview"          # for Gemini choose model from here: https://ai.google.dev/gemini-api/docs/models
  active_prompt_key: "PROMPT_4"                 # PROMPT_4 = ringing sheets; PROMPT_5 = retrap sheets; PROMPT_1,2 = sightings

  run_name: "ringing-subalpine_warblers-full"   # you choose name - this name will be saved in metadata and files will be named after this
  active_adapter: "cloudflare"                  # cloudflare | local    - it says where it will look for images
  local_root:                                   # --> if you set "active_adapter" to local then set this value
  cloudflare:                                   # --> if you set "active_adapter" to cloudflare then set this values

  image_upload_method: "file_api"               # base64 | file_api | pass_a_url (file_api for gemini and openai, base64 for gemini and openai, pass_a_url for openai only) --> basically file_api is the best option
  batch_operation_mode: "generate_and_upload"   # generate | upload | generate_and_upload | try_again_upload  ---> basically here you can do all needed steps seperatly or all steps in one go
                                                # --> generate --> will prepare jsonl file into "data_output/batches_out" without uploading it to API (in batch file = one line is one request)
                                                # --> upload --> will just upload already generated jsonl file to API (skip generating)
                                                # --> generate_and_upload --> will do both 
                                                # --> try_again_upload --> if some requests are not successfull in API processing then this will upload again only those unsuccessfull (good for timeout errors, not for bad structure of request errors)
  
  upload_files: []                              # for "generate_and_upload" can stay empty; for "upload" or "try_again_upload" must be file included here


  processing.mode: "batches"                    # all | batches | explicit   --> here you setup if it will work with whole batches or with specific images (both you need to specify bellow in config) 
  processing.allowed_batches: ["batch_10"]      # this is used if "mode: batches"  --> you select which batches to be uploaded 
  
  processing.allowed_images: ["img_9_43", "img_8_100", "img_7_11"]   # this is used if "mode: explicit"  --> you select which images to be uploaded






PROCESS BATCHES (downloading them from AI provider)

1. check if the batch is completed in from AI provider side
    --> you can use prepared tool "list_batches_G.py" (only for Gemini) or you can log in to GUI platform of OpenAI
2. you need to setup config.yaml for the specific type of data which will be processed (how to setup config is explained bellow OR read comments in config.yaml)
3. inside the file "get_OCR_results.py" you select which batch to be downloaded (batch_id) and mode of processing
4. run "get_OCR_results.py" (python get_OCR_results.py)
5. if run is successfull --> it will create folder with data here: "data_output/output"
   if run is not successfull completely --> it will tell you what was wrong here: "data_output/state/problems_to_deal_with-{NAME}.jsonl"

6. OPTIONAL: for ringing sheets: you can run script "/extra_tools/silent_errors.py"  --> which is supposed to discover some strange values which out of expectation to flag them
7. OPTIONAL: for ringing sheets: you can upload data to ChatGPT or Claude with proper prompt to discover some unusual data, not expected data, different data to flag them (use the most clever option of AI like Thinking etc..)
8. OPTIONAL: you can convert JSONL data into CSV structure (you can use "/extra_tools/json2csv_v4.py")


what to setup in config.yaml for get_OCR_results.py: 

  active_provider: "gemini"                     # openai | gemin     --> here you choose from where it will download your batch
  run_name: "ringing-subalpine_warblers-full"   # you choose name - SHOULD BE SAME AS THE ONE YOU USED FOR UPLOADING BATCH. This name will be saved in metadata and files will be named after this
  validation: (example for ringing)
    active_validator: "ocr_ringing"             # ocr_sightings | ocr_ringing | ocr_retrap
                                                # basically the most important thing - by this setting you select which file to be used in folder /validators/...
                                                # it depends what data you are processing - now there is prepared workflow for ringing sheets, retrap cards and sightings

    ring_no_max_suffix_length: 3                # very important setting - it says how many digits of suffix are usually in ring_no column   (3 = .012; 4 = .2329) 

    backfill_species: "Subalpine Warbler"       # AUTO | "Moorhen" | false (default)    #for missing names on other side of scan - it will take a name from first side of the scan (basically from previes image)
                                                # if whole batch is one species then you can explicitly name this species here otherwise AUTO

    ring_no_mode: AUTO                          # for ringing (not retrap) is the best to use AUTO as there are complex rules how to rate ring_no (and how to flag suspicious)                  
                                                # AUTO | MANUAL --> manual option use the regex bellow in config



Folder structure of project - explanation what is what: 
-------------------------------------------------------

.env                            ---> API KEY HERE 
config.yaml                     ---> configuration for both "make_OCR_batch.py" and "get_OCR_results.py"
prompts.yaml                    ---> user and system prompts based on structure of images (retraps, sightings, ringing)
requirements.txt                ---> libraries needed to be installed to run the program (pip install requirements.txt)
processed_items.txt             ---> logs with info what was processed (on image level)

make_OCR_batch.py               ---> first part of workflow - create batches which to be processed by AI provider
get_OCR_results.py              ---> second part of workflow - download and process and validate data - create data_outputs - files for review

Folders: 
    ai_providers                ---> it contains scripts for individual AI providers (Google Gemini; OpenAI; Local Ollama). Communication with API is here.
    data_output/batches_out     ---> it contains .jsonl file with requests which are then sent to AI provider API. One line = One request = One image
    data_output/output          ---> it contains foledrs with runs of get_OCR_results.py script --> results from AI processing and validationg data and review files with flagged cases
    data_output/raw             ---> it contains raw results from AI provider before processing them and validating them - just bare API response with raw data (only activated if mode="full" in get_OCR_results.py)
    data_output/state           ---> it contains file with logs of what was processed (batch level) and possible "problems_to_deal_with" (this is generated only if there are problems)
    docs                        ---> complex documentation generated from AI (not Jonas work)
    extra_tools                 ---> a bit of mess --> scripts outside of workflow which were used for some "data manipulation", post analyzation, etc. -- some can be still usefull (like converting jsonl csv script)
    image_management            ---> it contains scripts which manipulate with actuall image files (API cloudflare OR local storage)
    lexicon                     ---> it contains all dictionaries which are used for matching output with possible values (locations; series; species with meassurments)
    validators                  ---> most important files inside this folder --> all the logic of validations and data processing from AI providers are here. You can add new validators if you respect structure.
    z_archived_data             ---> old data - testing data - mess 


