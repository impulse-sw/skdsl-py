# Python DSL-to-FastAPI Translator (skdsl-py)

## Overview

`skdsl-py` is a utility to translate a domain-specific language (DSL) for API descriptions into Python FastAPI code. It helps in rapidly prototyping and generating the boilerplate for FastAPI applications based on a simple, text-based API contract.

This tool is inspired by its Rust counterpart [SK DSL](https://github.com/impulse-sw/cc-services/blob/stable/cc-server-kit-dsl/README.md) and aims to provide similar functionality for the Python ecosystem, specifically targeting FastAPI for web framework generation and Pydantic for data validation.

## Features

* **Simple DSL**: Define your API contract using a straightforward, line-based DSL.
* **Code Generation**: Automatically generates FastAPI routers, Pydantic models (or type hints), and endpoint function shells.
* **Type Definitions**: Support for defining custom data types, which can be translated to Pydantic models or Python type aliases.
* **Complex Requirements**: Define reusable sets of parameters (like headers or cookies) to apply across multiple endpoints or entire API tags.
* **OpenAPI Integration**: Generated FastAPI code automatically benefits from OpenAPI schema generation (`include_in_schema` for hidden elements).
* **Versioned Output**: Organizes generated code into versioned directories.
* **CLI Interface**: Easy-to-use command-line options for specifying input, output, versioning, and regeneration.

## DSL Specification

The `skdsl-py` client parses a text input file, processing lines that begin with specific keywords: `type`, `req`, or `api`.

### 1. Type Definitions (`type`)

Define types used in your API. These can be aliases to standard Python types/Pydantic models or hints for generating new Pydantic models.

* **Import-like usage**:
    ```dsl
    # Becomes: from your_project.custom_types import MyCustomType
    type MyCustomType your_project.custom_types.MyCustomType
    ```
    ```dsl
    # Becomes: from your_project.models import ComplexRequestModel as MyType
    type MyType your_project.models.ComplexRequestModel
    ```
* **Alias to Python/Pydantic types**:
    ```dsl
    # Becomes: MyTypeList = List[int] (in Python with typing.List)
    type MyTypeList Vec<i32>
    ```
    ```dsl
    # Becomes: StringKeyMap = Dict[str, float] (in Python with typing.Dict)
    type StringKeyMap HashMap<String, f64>
    ```
    (The translator will map DSL primitive types like `i32`, `String`, `Vec`, `HashMap` to their Python equivalents: `int`, `str`, `List`, `Dict`).

### 2. Requirement Types

Define incoming request parameters and outgoing response characteristics.

**Incoming Requirements:**

* **HTTP Method & Path**: `<get|post|put|patch|delete>/<path_segment>[/{type/param_name}|/{**rest_param_name}]...`
    * Example: `get/users/{u64/id}`
    * Example: `post/files/upload/{**filepath}`
    * Note: Only one HTTP method/path can be specified per endpoint.
* **Request Header**: `h/<type>/<name>` (e.g., `h/String/X-API-Key`)
* **Request Cookie**: `c/<key>` (e.g., `c/session_id`)
* **Request Query Parameter**: `q/<type>/<key>` (e.g., `q/i32/page_number`)
* **Request Body**:
    * `b/json/<TypeName>` (e.g., `b/json/UserData` - `UserData` should be a defined Pydantic model).
    * `b/file/<form_key>` (e.g., `b/file/upload` - for file uploads).
* **Request Form Key**: `f/<type>/<key>` (e.g., `f/String/username` - for form data).
* Note: You cannot use a general request body (`b/...`) and form keys (`f/...`) in the same endpoint.

**Outgoing Requirements:**

* **Response Body**:
    * `ok` (HTTP 200/204 with no specific body content, or a simple success message).
    * `b/plain` (Plain text response).
    * `b/html` (HTML response).
    * `b/file` (File download response).
    * `b/json/<TypeName>` (e.g., `b/json/UserProfile` - `UserProfile` should be a Pydantic model).
* **Response Header**: `h/<type>/<name>` (e.g., `h/String/X-Request-ID`).
* **Response Cookie**: `c/<key>` (e.g., `c/tracking_cookie`).

### 3. API Tag and Endpoints (`api`)

* **Define an API Tag**:
    ```dsl
    api tag <tag_name> [req/<requirement_name>...]
    ```
    All endpoints listed after this line will be grouped under this tag. This typically translates to a FastAPI `APIRouter` and a Python file named `{tag_name}.py`.
* **Define an Endpoint**:
    ```dsl
    api [req/<requirement_name>...] <incoming_http_method_and_path> [other_incoming_requirements...] -> <outgoing_response_body> [other_outgoing_requirements...]
    ```
    Example:
    ```dsl
    api tag chat
    api get/chats q/i64/offset q/i32/limit -> b/json/Vec<ChatData>
    api get/chat/{u64/id} -> b/json/ChatData
    api post/chat/{str/room_id}/message b/json/MessagePayload -> ok h/String/X-Message-ID
    ```

### 4. Complex Requirements (`req`)

Define reusable sets of incoming and/or outgoing requirements (excluding body, form, or path parameters).
```dsl
req <requirement_name> <incoming_headers_cookies_queries...> [-> <outgoing_headers_cookies...>]
````

Example:

```dsl
req auth_tokens h/String/X-Auth-Token c/session_token -> h/String/X-Session-Status
```

Use them in API tags or individual endpoints:

```dsl
api tag user_profiles req/auth_tokens
api req/auth_tokens get/users/me -> b/json/UserPrivateProfile

api tag public_data
api get/items -> b/json/Vec<Item>
# Endpoint-specific complex requirement
api req/pagination_params get/items_paged q/i32/custom_filter -> b/json/PagedItems
```

### 5\. Hidden APIs and Requirements

  * **Hidden API Endpoint**: `api/hidden ...`
      * Excludes the endpoint from the generated OpenAPI specification (FastAPI's `include_in_schema=False`).
  * **Hidden Complex Requirement**: `req/hidden <requirement_name> ...`
      * Defines requirements primarily for OpenAPI documentation without necessarily enforcing them as strict, usable parameters in the generated function signature if they are not directly referenced. The exact behavior might depend on the translator's implementation for parameter generation.

## Installation

1.  **Prerequisites**:

      * Python 3.8+
      * `pip`

2.  **Setup**:

    ```bash
    git clone <repository_url_for_skdsl-py>
    cd skdsl-py
    pip install .
    # or for development
    # pip install -e .
    ```

    (If it's a single script, you might run it directly: `python skdsl_py/main.py ...`)

## Usage

The translator is used via the `skdsl-py` command-line interface.

```
Usage: skdsl-py [OPTIONS] --input <FILE> --output <FOLDER>

Options:
  -i, --input <FILE>       Input DSL file
  -o, --output <FOLDER>    Output folder for generated FastAPI code
  -v, --version <VERSION>  API version (e.g., v1, v2). Optional.
  -r, --regenerate         Don't attempt to bump the API version automatically; 
                           instead, use the specified version (or latest/default) 
                           and rewrite all generated files.
  -h, --help               Print help
```

**Examples**:

  * Generate API version `v1` into the `generated_api` folder:

    ```bash
    skdsl-py -i my_api_v1.dsl -o generated_api
    ```

  * Manually specify API version `v2` using a different DSL file:

    ```bash
    skdsl-py -i my_api_v2.dsl -o generated_api -v v2
    ```

  * Regenerate API version `v1` from scratch, overwriting existing files:

    ```bash
    skdsl-py -i my_api_v1.dsl -o generated_api -v v1 -r
    ```

## Generated Output Structure

`skdsl-py` generates a directory structure for your FastAPI application:

```
<output_folder>/
└──v1/                             # API version
   ├── __init__.py                 # Makes the version folder a Python package
   ├── models.py                   # Pydantic models and type aliases from 'type' definitions
   ├── main_app.py                 # Main FastAPI app for this version, includes all routers
   ├── users.py                    # FastAPI router for 'users' tag
   ├── chats.py                    # FastAPI router for 'chats' tag
   └── files.py                    # FastAPI router for 'files' tag
└──v2/
   ├── ...
└──.api.json                       # (Potentially per version) Serialized DSL for version tracking
```

  * **`{tag_name}.py`**: Contains a FastAPI `APIRouter` with endpoints defined under that tag.
  * **`models.py`**: Contains Pydantic model definitions and Python type aliases derived from `type` directives in the DSL.
  * **`main_app.py`** (or similar): A central FastAPI application file that includes all the generated routers for that specific API version.
  * **`__init__.py`**: Standard Python file to mark directories as packages.

## Notes on Breaking Changes

The original `skdsl` tool has a mechanism to detect breaking changes and suggest version bumps. For non-breaking changes, you can generally:

  * Add new API tags or new endpoints.
  * Modify existing endpoints by:
      * Removing incoming requirements (making them optional or removing them).
      * Adding new outgoing requirements (e.g., a new optional response header).

The Python version aims for similar considerations, though automatic breaking change detection might be implemented progressively. Using the `-r` (regenerate) flag allows for explicit control over file generation.
