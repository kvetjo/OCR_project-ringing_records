"""
Provider Selector
Dynamically loads and returns the appropriate AI provider based on configuration.
This allows adding new providers without modifying main scripts.

Every returned dictionary is checked against
provider_interface.REQUIRED_PROVIDER_FUNCTIONS, so a provider that is missing
a function is reported here instead of failing later in the pipeline.
"""

from typing import Dict, Any

from ai_providers.provider_interface import REQUIRED_PROVIDER_FUNCTIONS


def check_provider_functions(provider_name: str, functions: Dict[str, Any]) -> None:
    """
    Verify that a provider dictionary fulfils the interface contract.

    Args:
        provider_name: Name of the provider (only used in the error message)
        functions: The dictionary returned by get_provider()

    Raises:
        NotImplementedError: If a required key is missing or is not callable
    """
    missing = [
        name for name in REQUIRED_PROVIDER_FUNCTIONS
        if name not in functions
    ]

    not_callable = [
        name for name in REQUIRED_PROVIDER_FUNCTIONS
        if name in functions and not callable(functions[name])
    ]

    if missing or not_callable:
        problems = []

        if missing:
            problems.append(f"missing functions: {missing}")

        if not_callable:
            problems.append(f"not callable: {not_callable}")

        raise NotImplementedError(
            f"Provider '{provider_name}' does not fulfil the provider interface - "
            + "; ".join(problems)
            + ". See ai_providers/provider_interface.py for the full contract."
        )


def get_provider(provider_name: str) -> Dict[str, Any]:
    """
    Dynamically load and return provider functions based on provider name.

    Args:
        provider_name: Name of the provider ("gemini", "openai", "ollama",
            "ollama-ssh")

    Returns:
        Dictionary containing all provider functions

    Raises:
        ValueError: If provider_name is not recognized
        NotImplementedError: If the provider does not fulfil the interface
    """
    provider_name = provider_name.lower()

    if provider_name == "gemini":
        from ai_providers.gemini_provider import (
            load_config,
            get_image_adapter,
            load_prompts,
            make_jsonl_line,
            get_request_custom_id,
            extract_batch_page_ids,
            build_jsonl_batches,
            upload_image_to_gemini,
            upload_file_to_gemini,
            create_batch_job,
            find_jsonl_files_in_output,
            is_in_allowed_batch,
            save_processed_batches,
            load_processed_batches,
            list_batches,
            get_batch,
            download_file,
            parse_gemini_batch_results,
            extract_text_from_gemini_record,
            get_gemini_batch_status,
            get_batch_id,
            log_problem,
            is_error_record,
            get_batch_tokens,
            get_tokens_from_records,
            get_processing_time,
            get_model_name,
            get_batch_created_time,
            get_batch_completed_time,
            get_request_counts,
            parse_error_records,
            normalize_batch_name,
            batch_created_after,
            download_raw_batch,
            parse_raw_content
        )

        functions = {
            'load_config': load_config,
            'get_image_adapter': get_image_adapter,
            'load_prompts': load_prompts,
            'make_jsonl_line': make_jsonl_line,
            'get_request_custom_id': get_request_custom_id,
            'extract_batch_page_ids': extract_batch_page_ids,
            'build_jsonl_batches': build_jsonl_batches,
            'upload_image_to_provider': upload_image_to_gemini,
            'upload_file_to_provider': upload_file_to_gemini,
            'create_batch_job': create_batch_job,
            'find_jsonl_files_in_output': find_jsonl_files_in_output,
            'is_in_allowed_batch': is_in_allowed_batch,
            'save_processed_batches': save_processed_batches,
            'load_processed_batches': load_processed_batches,
            'list_batches': list_batches,
            'get_batch': get_batch,
            'download_file': download_file,
            'parse_batch_results': parse_gemini_batch_results,
            'extract_text_from_record': extract_text_from_gemini_record,
            'get_batch_status': get_gemini_batch_status,
            'get_batch_id': get_batch_id,
            'log_problem': log_problem,
            'is_error_record': is_error_record,
            'get_batch_tokens': get_batch_tokens,
            'get_tokens_from_records': get_tokens_from_records,
            'get_processing_time': get_processing_time,
            'get_model_name': get_model_name,
            'get_batch_created_time': get_batch_created_time,
            'get_batch_completed_time': get_batch_completed_time,
            'get_request_counts': get_request_counts,
            'parse_error_records': parse_error_records,
            'normalize_batch_name': normalize_batch_name,
            'batch_created_after': batch_created_after,
            'download_raw_batch': download_raw_batch,
            'parse_raw_content': parse_raw_content
        }

        check_provider_functions(provider_name, functions)
        return functions

    elif provider_name == "openai":
        from ai_providers.openai_provider import (
            load_config,
            get_image_adapter,
            load_prompts,
            make_jsonl_line,
            get_request_custom_id,
            extract_batch_page_ids,
            build_jsonl_batches,
            upload_image_to_openai,
            upload_file_to_provider,
            create_batch_job,
            find_jsonl_files_in_output,
            is_in_allowed_batch,
            save_processed_batches,
            load_processed_batches,
            list_batches,
            get_batch,
            download_file,
            parse_openai_batch_results,
            extract_text_from_openai_record,
            get_openai_batch_status,
            get_batch_id,
            log_problem,
            is_error_record,
            get_batch_tokens,
            get_tokens_from_records,
            get_processing_time,
            get_model_name,
            get_batch_created_time,
            get_batch_completed_time,
            get_request_counts,
            parse_error_records,
            normalize_batch_name,
            batch_created_after,
            download_raw_batch,
            parse_raw_content
        )

        functions = {
            'load_config': load_config,
            'get_image_adapter': get_image_adapter,
            'load_prompts': load_prompts,
            'make_jsonl_line': make_jsonl_line,
            'get_request_custom_id': get_request_custom_id,
            'extract_batch_page_ids': extract_batch_page_ids,
            'build_jsonl_batches': build_jsonl_batches,
            'upload_image_to_provider': upload_image_to_openai,
            'upload_file_to_provider': upload_file_to_provider,
            'create_batch_job': create_batch_job,
            'find_jsonl_files_in_output': find_jsonl_files_in_output,
            'is_in_allowed_batch': is_in_allowed_batch,
            'save_processed_batches': save_processed_batches,
            'load_processed_batches': load_processed_batches,
            'list_batches': list_batches,
            'get_batch': get_batch,
            'download_file': download_file,
            'parse_batch_results': parse_openai_batch_results,
            'extract_text_from_record': extract_text_from_openai_record,
            'get_batch_status': get_openai_batch_status,
            'get_batch_id': get_batch_id,
            'log_problem': log_problem,
            'is_error_record': is_error_record,
            'get_batch_tokens': get_batch_tokens,
            'get_tokens_from_records': get_tokens_from_records,
            'get_processing_time': get_processing_time,
            'get_model_name': get_model_name,
            'get_batch_created_time': get_batch_created_time,
            'get_batch_completed_time': get_batch_completed_time,
            'get_request_counts': get_request_counts,
            'parse_error_records': parse_error_records,
            'normalize_batch_name': normalize_batch_name,
            'batch_created_after': batch_created_after,
            'download_raw_batch': download_raw_batch,
            'parse_raw_content': parse_raw_content
        }

        check_provider_functions(provider_name, functions)
        return functions

    elif provider_name in ("ollama", "ollama-ssh"):
        # Ollama has no batch API. make_OCR_batch.py runs the images through
        # run_local_batch() and writes the finished batch straight into
        # directories.raw_dir_base; get_OCR_results.py reads it from there.
        # The batch API half of the contract is present as stubs that explain
        # this when called.
        from ai_providers.base_ollama import (
            load_config,
            get_image_adapter,
            load_prompts,
            make_jsonl_line,
            get_request_custom_id,
            extract_batch_page_ids,
            build_jsonl_batches,
            upload_image_to_provider,
            upload_file_to_provider,
            create_batch_job,
            find_jsonl_files_in_output,
            is_in_allowed_batch,
            save_processed_batches,
            load_processed_batches,
            list_batches,
            get_batch,
            download_file,
            parse_batch_results,
            extract_text_from_record,
            get_batch_status,
            get_batch_id,
            log_problem,
            is_error_record,
            get_batch_tokens,
            get_tokens_from_records,
            get_processing_time,
            get_model_name,
            get_batch_created_time,
            get_batch_completed_time,
            get_request_counts,
            parse_error_records,
            normalize_batch_name,
            batch_created_after,
            download_raw_batch,
            parse_raw_content,
            run_local_batch
        )

        functions = {
            'load_config': load_config,
            'get_image_adapter': get_image_adapter,
            'load_prompts': load_prompts,
            'make_jsonl_line': make_jsonl_line,
            'get_request_custom_id': get_request_custom_id,
            'extract_batch_page_ids': extract_batch_page_ids,
            'build_jsonl_batches': build_jsonl_batches,
            'upload_image_to_provider': upload_image_to_provider,
            'upload_file_to_provider': upload_file_to_provider,
            'create_batch_job': create_batch_job,
            'find_jsonl_files_in_output': find_jsonl_files_in_output,
            'is_in_allowed_batch': is_in_allowed_batch,
            'save_processed_batches': save_processed_batches,
            'load_processed_batches': load_processed_batches,
            'list_batches': list_batches,
            'get_batch': get_batch,
            'download_file': download_file,
            'parse_batch_results': parse_batch_results,
            'extract_text_from_record': extract_text_from_record,
            'get_batch_status': get_batch_status,
            'get_batch_id': get_batch_id,
            'log_problem': log_problem,
            'is_error_record': is_error_record,
            'get_batch_tokens': get_batch_tokens,
            'get_tokens_from_records': get_tokens_from_records,
            'get_processing_time': get_processing_time,
            'get_model_name': get_model_name,
            'get_batch_created_time': get_batch_created_time,
            'get_batch_completed_time': get_batch_completed_time,
            'get_request_counts': get_request_counts,
            'parse_error_records': parse_error_records,
            'normalize_batch_name': normalize_batch_name,
            'batch_created_after': batch_created_after,
            'download_raw_batch': download_raw_batch,
            'parse_raw_content': parse_raw_content,

            # Beyond the interface. 'local_runtime' tells the two main scripts
            # that this provider produces its results on this machine: there is
            # no API key to check and nothing to download, the finished batch
            # is already a file in raw_dir_base.
            'local_runtime': True,
            'run_local_batch': run_local_batch
        }

        check_provider_functions(provider_name, functions)
        return functions

    else:
        raise ValueError(
            f"Unknown provider: {provider_name}. "
            f"Available providers: gemini, openai, ollama, ollama-ssh. "
            f"To add a new provider, create a new file in ai_providers/ "
            f"and add it to provider_selector.py."
        )
