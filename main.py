#!/usr/bin/env python

import argparse
import json
import os
from pathlib import Path
from typing import List, Dict, Optional, Union, Any

from pydantic import BaseModel, Field

# --- Entities (Normally in a separate entities.py) ---
class DslTypeDefinition(BaseModel):
    name: str
    definition: str
    is_alias: bool
    # For Python codegen
    py_type_str: Optional[str] = None
    py_import_stmt: Optional[str] = None
    pydantic_model_def: Optional[str] = None

class DslParameter(BaseModel): # Common fields for incoming/outgoing params
    param_type: str # 'header', 'query', 'cookie', 'path', 'body_json', 'body_file', 'form_param', etc.
    name: str # Name of header, query key, path variable, cookie name, form key
    dsl_type: str # Original DSL type string (e.g., "u64", "Vec<String>", "MyData")
    py_type: Optional[str] = None # Translated Python type (e.g., "int", "List[str]", "MyData")
    is_hidden: bool = False # For OpenAPI spec
    # For body/file specifically
    content_type: Optional[str] = None # e.g. application/json, application/octet-stream
    # For path params
    is_rest_path: bool = False # for {**rest_path}

class DslBody(BaseModel):
    body_type: str # 'json', 'msgpack', 'file', 'plain', 'html', 'ok'
    dsl_type: Optional[str] = None # For json/msgpack, e.g., "MyData", "Vec<OtherData>"
    py_type: Optional[str] = None # Translated Python type
    file_form_key: Optional[str] = None # For b/file/<form_key>

class DslEndpoint(BaseModel):
    raw_definition: str
    is_hidden_openapi: bool = False # from api/hidden
    
    http_method: str
    path_template: str # e.g., "/users/{id}"
    
    path_params: List[DslParameter] = []
    query_params: List[DslParameter] = []
    header_params: List[DslParameter] = []
    cookie_params: List[DslParameter] = []
    form_params: List[DslParameter] = [] # from f/<type>/<key>
    
    request_body: Optional[DslBody] = None # Parsed from b/...
    
    response_body: DslBody # Parsed from -> b/... or -> ok
    response_headers: List[DslParameter] = []
    response_cookies: List[DslParameter] = []
    
    # For linking and generation
    func_name: str = ""
    complex_req_names: List[str] = []
    
    # Populated after resolving complex requirements
    final_path_params: List[DslParameter] = Field(default_factory=list)
    final_query_params: List[DslParameter] = Field(default_factory=list)
    final_header_params: List[DslParameter] = Field(default_factory=list)
    final_cookie_params: List[DslParameter] = Field(default_factory=list)
    final_form_params: List[DslParameter] = Field(default_factory=list)
    final_request_body: Optional[DslBody] = None
    
    final_response_body: Optional[DslBody] = None
    final_response_headers: List[DslParameter] = Field(default_factory=list)
    final_response_cookies: List[DslParameter] = Field(default_factory=list)


class DslComplexRequirement(BaseModel):
    name: str
    is_hidden_openapi: bool # From req/hidden
    
    # Note: DSL spec says complex reqs cannot have body, form, path params [cite: 23]
    header_params: List[DslParameter] = []
    query_params: List[DslParameter] = [] # Though spec doesn't explicitly list, it could be useful
    cookie_params: List[DslParameter] = []
    
    response_headers: List[DslParameter] = []
    response_cookies: List[DslParameter] = []


class DslTag(BaseModel):
    name: str
    # Filename for this tag, e.g., users.py
    py_module_name: str = ""
    # Requirements applied to all endpoints in this tag
    complex_req_names: List[str] = []
    endpoints: List[DslEndpoint] = []

class DslFile(BaseModel):
    type_definitions: Dict[str, DslTypeDefinition] = {} # name: DslTypeDefinition
    complex_requirements: Dict[str, DslComplexRequirement] = {} # name: DslComplexRequirement
    tags: List[DslTag] = []
    # For generating a models.py or types.py
    pydantic_models_code: str = ""
    type_aliases_code: str = ""
    custom_imports_code: str = ""


# --- Type Translation (Normally in a separate type_translator.py) ---
def translate_dsl_primitive_type_to_python(dsl_type: str) -> Optional[str]:
    mapping = {
        "str": "str", "String": "str",
        "i8": "int", "u8": "int", "i16": "int", "u16": "int",
        "i32": "int", "u32": "int", "i64": "int", "u64": "int",
        "f32": "float", "f64": "float",
        "bool": "bool",
    }
    return mapping.get(dsl_type)

def parse_complex_dsl_type(dsl_type: str) -> Dict[str, Any]:
    """
    Parses types like "Vec<MyType>", "HashMap<String, u32>"
    Returns a dict like {'container': 'List', 'inner': 'MyType'} or
                       {'container': 'Dict', 'key_type': 'str', 'value_type': 'int'}
                       or {'type': 'MySimpleType'}
    This is a simplified parser. A robust one would use regex or a proper tokenizer.
    """
    dsl_type = dsl_type.strip()
    if dsl_type.startswith("Vec<") and dsl_type.endswith(">"):
        inner = dsl_type[4:-1]
        return {"container": "List", "inner_dsl": inner}
    if dsl_type.startswith("HashMap<") and dsl_type.endswith(">"):
        parts = dsl_type[8:-1].split(",", 1)
        if len(parts) == 2:
            return {"container": "Dict", "key_dsl": parts[0].strip(), "value_dsl": parts[1].strip()}
    # Add more complex types if needed (Option, Result etc.)
    return {"type": dsl_type}


def dsl_type_to_python_type_str(dsl_type_name: str, defined_types: Dict[str, DslTypeDefinition]) -> str:
    """Converts a DSL type string to a Python type string, resolving custom types."""
    parsed_generic = parse_complex_dsl_type(dsl_type_name)

    if "container" in parsed_generic:
        container = parsed_generic["container"]
        if container == "List":
            inner_py = dsl_type_to_python_type_str(parsed_generic["inner_dsl"], defined_types)
            return f"List[{inner_py}]"
        elif container == "Dict":
            key_py = dsl_type_to_python_type_str(parsed_generic["key_dsl"], defined_types)
            value_py = dsl_type_to_python_type_str(parsed_generic["value_dsl"], defined_types)
            return f"Dict[{key_py}, {value_py}]"
    else: # Simple type or custom type
        simple_type = parsed_generic["type"]
        primitive_py = translate_dsl_primitive_type_to_python(simple_type)
        if primitive_py:
            return primitive_py
        if simple_type in defined_types: # It's a custom defined type
            return simple_type # Use the name, Pydantic will handle it
    
    # Fallback or error
    # print(f"Warning: Could not fully translate DSL type '{dsl_type_name}' to Python. Using 'Any'.")
    return "Any" # Fallback for unknown types

def generate_pydantic_model_for_dsl_type(type_def: DslTypeDefinition, defined_types: Dict[str, DslTypeDefinition]) -> str:
    """
    Generates a Pydantic model string if the type is not a simple alias to existing Python types.
    Example: `type MyType crate::types::MyType` would ideally mean MyType is already a Pydantic model.
             `type MyTypeAlias HashMap<String, u32>` is a type alias.
             This function is more for types that are implicitly defined as structures by usage,
             or if `crate::types::MyType` implies a structure to be mirrored.
             For this translator, we'll assume `type Name some.path` is an import,
             and `type Name AliasDefinition` is a Python type alias.
             Actual Pydantic models would be assumed to exist or defined manually if they are complex.
             The Rust DSL's `type MyType crate::types::MyType` suggests an existing type[cite: 8].
             If `MyType` is used as a request/response body, it MUST be a Pydantic model.
    """
    if type_def.is_alias: # e.g. type MyList Vec<i32>
        py_equiv = dsl_type_to_python_type_str(type_def.definition, defined_types)
        type_def.py_type_str = f"{type_def.name} = {py_equiv}"
        return "" # It's an alias, not a new model
    else: # e.g. type User crate::models::User
          # This implies User is a Pydantic model defined elsewhere or needs to be.
          # For now, we'll assume it means an import.
        parts = type_def.definition.split("::")
        if len(parts) > 1:
            module_path = ".".join(parts[:-1])
            class_name = parts[-1]
            if class_name == type_def.name:
                type_def.py_import_stmt = f"from {module_path} import {class_name}"
            else:
                type_def.py_import_stmt = f"from {module_path} import {class_name} as {type_def.name}"
            type_def.py_type_str = type_def.name # The type is now available via import
        else: # Not a path, maybe an opaque type that should be a Pydantic model
              # For simplicity, we create a placeholder Pydantic model
            type_def.pydantic_model_def = f"class {type_def.name}(BaseModel):\n    pass # TODO: Define fields for {type_def.name}\n"
            type_def.py_type_str = type_def.name
        return type_def.pydantic_model_def or ""


# --- DSL Parser (Normally in a separate dsl_parser.py) ---

def parse_requirement_item(item_str: str, is_outgoing: bool) -> Union[DslParameter, DslBody, None]:
    parts = item_str.split('/')
    if not parts: return None

    prefix = parts[0]
    
    # Incoming
    if not is_outgoing:
        if prefix == 'h' and len(parts) == 3: # h/<type>/<name> [cite: 8]
            return DslParameter(param_type='header', name=parts[2], dsl_type=parts[1])
        elif prefix == 'c' and len(parts) == 2: # c/<key> [cite: 8]
            return DslParameter(param_type='cookie', name=parts[1], dsl_type='str') # Cookies are strings by default
        elif prefix == 'q' and len(parts) == 3: # q/<type>/<key> [cite: 8]
            return DslParameter(param_type='query', name=parts[2], dsl_type=parts[1])
        elif prefix == 'b': # b/<json|msgpack>/<type> or b/file/<form_key> [cite: 8]
            if len(parts) == 3:
                body_format = parts[1]
                if body_format in ['json', 'msgpack']:
                    return DslBody(body_type=body_format, dsl_type=parts[2])
                elif body_format == 'file':
                    return DslBody(body_type='file', file_form_key=parts[2])
        elif prefix == 'f' and len(parts) == 3: # f/<type>/<key> [cite: 8]
            return DslParameter(param_type='form_param', name=parts[2], dsl_type=parts[1])
        elif prefix in ["get", "post", "put", "patch", "delete"]: # <method>/<path_spec>
            # This is handled separately as it defines the core path and method
            return None 
    
    # Outgoing
    if is_outgoing:
        if prefix == 'ok' and len(parts) == 1: # ok [cite: 8]
            return DslBody(body_type='ok')
        elif prefix == 'h' and len(parts) == 3: # h/<type>/<name> [cite: 9]
            return DslParameter(param_type='header', name=parts[2], dsl_type=parts[1])
        elif prefix == 'c' and len(parts) == 2: # c/<key> [cite: 9]
            return DslParameter(param_type='cookie', name=parts[1], dsl_type='str')
        elif prefix == 'b': # b/<plain|html|file> or b/<json|msgpack>/<type> [cite: 8]
            if len(parts) >= 2:
                body_format = parts[1]
                if body_format in ['plain', 'html', 'file'] and len(parts) == 2:
                     return DslBody(body_type=body_format)
                elif body_format in ['json', 'msgpack'] and len(parts) == 3:
                    return DslBody(body_type=body_format, dsl_type=parts[2])
    return None


def parse_api_endpoint_line(line: str, defined_types: Dict[str, DslTypeDefinition]) -> Optional[DslEndpoint]:
    # Example: api get/chats q/i64/chat_id -> b/json/Vec<ChatData> [cite: 14]
    # Example: api/hidden post/submit -> ok [cite: 19]
    # Example: api req/master get/test -> ok c/C3A-Sign [cite: 22]
    
    parts = line.split()
    if not parts: return None

    is_hidden_openapi = False
    if parts[0] == "api/hidden":
        is_hidden_openapi = True
        parts.pop(0)
    elif parts[0] == "api":
        parts.pop(0)
    else:
        return None # Not an api line

    complex_req_names = []
    while parts and parts[0].startswith("req/"): # [cite: 21]
        complex_req_names.append(parts[0].split('/', 1)[1])
        parts.pop(0)

    if not parts or "->" not in parts: return None # Must have path spec and response

    # Split into incoming and outgoing parts
    arrow_index = parts.index("->")
    incoming_dsl_parts = parts[:arrow_index]
    outgoing_dsl_parts = parts[arrow_index+1:]

    if not incoming_dsl_parts: return None # Must have at least path

    # First incoming part is method and path
    method_path_str = incoming_dsl_parts.pop(0)
    method_path_split = method_path_str.split('/', 1)
    if len(method_path_split) < 2: return None # e.g. get/ or just /path
    
    http_method = method_path_split[0].lower()
    path_spec = "/" + method_path_split[1] # Add leading slash if not already there

    path_params_list: List[DslParameter] = []
    path_template_parts = []
    current_path_segments = path_spec.strip('/').split('/')
    
    # Example path: {u64/id}/{**rest_path} [cite: 8]
    #               chats/by_id/{u64/id}
    #               chat/{str/host}/audio-request [cite: 14]
    
    i = 0
    while i < len(current_path_segments):
        segment = current_path_segments[i]
        if segment.startswith('{') and segment.endswith('}'):
            is_rest_path = False
            if segment.startswith('{**'): # {**rest_path}
                param_def = segment[3:-1]
                param_type = "String" # Rest paths are strings
                param_name = param_def
                is_rest_path = True
            elif i + 1 < len(current_path_segments) and current_path_segments[i+1].endswith('}'): # {type/name} over two segments (DSL specific)
                param_type = segment[1:]
                param_name = current_path_segments[i+1][:-1]
                i += 1 # Consume next part
            else: # {name} - assume type needs to be inferred or is default 'str' (FastAPI style)
                  # The DSL is specific: {type/name} or {name} if type is part of segment
                # For simplicity, if it's just {name}, we assume str. DSL uses {type/name}.
                # Let's adhere to DSL: type/name in different segments from path
                # The example `get/chat/{u64/id}` means "u64" is type, "id" is name.
                # The Rust code parses path template segment by segment
                # `path.path.replace(['/', '-'], "_").replace("{**", "by_").replace('{', "by_").replace('}', "")` [cite: 49] is for func name
                # For path parsing: `Incoming::Path(path)) = incoming.iter().find(|v| matches!(v, Incoming::Path(_)))` [cite: 49]
                # Path params are like: `path_param.key`, `path_param.r#type` from `parse_http_path`
                # Example: get/chat/{u64/id} -> path.params = [{type: "u64", key: "id"}] [cite: 14, 161]
                # This means the type is segment[i][1:] and name is segment[i+1][:-1]
                # This logic is tricky. Let's simplify: assume {name} and type is from next segment if current segment doesn't contain '/'
                
                # Corrected path param parsing based on `get/chat/{u64/id}` structure:
                # The DSL seems to put type and name inside the {} or across segments.
                # `/{u64/id}/` or `/chat/{u64/id}`
                # The path template for FastAPI should be `/chat/{id}`
                # The parameter `id` has type `u64`.

                # Re-evaluating path parameter extraction:
                # DSL: `get/actual/http/path/{u64/id}/{**rest_path}` [cite: 8]
                # Means segments like "id" (from "{u64/id}") or "rest_path" (from "{**rest_path}")
                if '/' in segment: # e.g. {u64/id}
                    content = segment[1:-1]
                    param_type, param_name = content.split('/',1)
                else: # e.g. {id} - this case is not explicitly in DSL spec for path params, but common in web frameworks.
                      # DSL uses {type/name}. So we assume if no '/', it's an error or needs clarification.
                      # For now, let's assume it's an error or default to str if this happens.
                      # Based on[cite: 161], type is from one part, name from next.
                      # Let's stick to the `{type/name}` or `{**name}` within one segment.
                    # print(f"Warning: Path segment '{segment}' format unclear, assuming name-only and type 'str'.")
                    param_type = "str"
                    param_name = segment[1:-1]
            
            path_params_list.append(DslParameter(param_type='path', name=param_name, dsl_type=param_type, is_rest_path=is_rest_path))
            path_template_parts.append(f"{{{param_name}}}")
        else:
            path_template_parts.append(segment)
        i += 1
    
    final_path_template = "/" + "/".join(path_template_parts) if path_template_parts else "/"
    if final_path_template == "//": final_path_template = "/"


    ep = DslEndpoint(
        raw_definition=line, 
        is_hidden_openapi=is_hidden_openapi,
        http_method=http_method,
        path_template=final_path_template,
        path_params=path_params_list,
        complex_req_names=complex_req_names,
        response_body=DslBody(body_type='ok') # Default, will be overwritten
    )

    # Parse other incoming items
    for item_str in incoming_dsl_parts:
        item = parse_requirement_item(item_str, is_outgoing=False)
        if isinstance(item, DslParameter):
            if item.param_type == 'query': ep.query_params.append(item)
            elif item.param_type == 'header': ep.header_params.append(item)
            elif item.param_type == 'cookie': ep.cookie_params.append(item)
            elif item.param_type == 'form_param': ep.form_params.append(item)
        elif isinstance(item, DslBody):
            if ep.request_body:
                print(f"Warning: Multiple request bodies defined for {line}. Using last one.") # [cite: 51]
            ep.request_body = item
    
    # Check constraints [cite: 11, 52]
    if ep.request_body and ep.form_params:
        print(f"Error: Cannot use body and form keys together in API: {line}")
        return None

    # Parse outgoing items
    has_response_body = False
    for item_str in outgoing_dsl_parts:
        item = parse_requirement_item(item_str, is_outgoing=True)
        if isinstance(item, DslParameter):
            if item.param_type == 'header': ep.response_headers.append(item)
            elif item.param_type == 'cookie': ep.response_cookies.append(item)
        elif isinstance(item, DslBody):
            if has_response_body:
                 print(f"Warning: Multiple response bodies defined for {line}. Using last one.") # [cite: 54]
            ep.response_body = item
            has_response_body = True
            
    if not has_response_body: # [cite: 53]
        print(f"Error: No response body provided for API: {line}")
        return None # Must have a response body

    # Generate function name (simplified from Rust version [cite: 49])
    name_parts = [ep.http_method]
    clean_path = ep.path_template.replace('{', '').replace('}', '').replace('**', '')
    name_parts.extend(s for s in clean_path.split('/') if s)
    ep.func_name = "_".join(name_parts) if name_parts else "root_handler"
    
    return ep


def parse_dsl_file_content(content: str) -> DslFile:
    dsl_file = DslFile()
    current_tag: Optional[DslTag] = None

    for line_num, raw_line in enumerate(content.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith('#'): # Skip empty lines and comments
            continue

        # [cite: 6] Lines start with type, req, or api
        if line.startswith("type "): # type MyType crate::types::MyType OR type MyList Vec<String> [cite: 8]
            parts = line.split(maxsplit=2)
            if len(parts) == 3:
                _, name, definition = parts
                # Heuristic: if definition contains "::" or starts with a lowercase module path, it's likely a usage import.
                # Otherwise, it's an alias for a potentially complex type.
                is_alias = not ("::" in definition or (definition[0].islower() and '.' in definition)) and not any(kw in definition for kw in ["class ", "struct "])
                
                # If definition is like "HashMap<String, u32>", it's an alias.
                # If "crate::types::MyType", it's a usage.
                # The Rust code: `if typedesc.contains("::")` [cite: 201] implies usage. Otherwise alias.
                is_alias_by_rust_logic = "::" not in definition

                type_def = DslTypeDefinition(name=name, definition=definition, is_alias=is_alias_by_rust_logic)
                dsl_file.type_definitions[name] = type_def
            else:
                print(f"Warning: Malformed type definition at line {line_num+1}: {line}")

        elif line.startswith("req"): # req[/hidden] <name> <incoming...> [-> <outgoing...>] [cite: 20]
            parts = line.split()
            is_hidden = parts[0] == "req/hidden"
            name_index = 1 if is_hidden else 1 # req <name> or req/hidden <name> -> name is parts[1]
            
            # The name of the requirement is at parts[1] after "req" or "req/hidden"
            # So, parts[0] is "req" or "req/hidden", parts[1] is name, parts[2:] are definitions
            if len(parts) < 2 :
                print(f"Warning: Malformed requirement definition at line {line_num+1}: {line}")
                continue
            
            req_name = parts[1]
            def_parts = parts[2:]

            cr = DslComplexRequirement(name=req_name, is_hidden_openapi=is_hidden)
            
            # current_parsing_list = cr.incoming_params_dsl if "->" not in def_parts or def_parts.index("->") > 0 else [] # Placeholder logic
            
            # Simplified parsing for complex reqs (no body/path/form [cite: 23])
            # Example: req master h/str/C3A-Access -> h/str/C3A-Sign [cite: 21]
            if "->" in def_parts:
                arrow_idx = def_parts.index("->")
                in_items_str = def_parts[:arrow_idx]
                out_items_str = def_parts[arrow_idx+1:]
            else:
                in_items_str = def_parts
                out_items_str = []

            for item_str in in_items_str:
                item = parse_requirement_item(item_str, is_outgoing=False)
                if isinstance(item, DslParameter):
                    if item.param_type == 'header': cr.header_params.append(item)
                    elif item.param_type == 'query': cr.query_params.append(item)
                    elif item.param_type == 'cookie': cr.cookie_params.append(item)
                    else: print(f"Warning: Invalid incoming item '{item_str}' in complex req '{req_name}' (line {line_num+1})")
                else: print(f"Warning: Invalid incoming item '{item_str}' in complex req '{req_name}' (line {line_num+1})")
            
            for item_str in out_items_str:
                item = parse_requirement_item(item_str, is_outgoing=True)
                if isinstance(item, DslParameter):
                    if item.param_type == 'header': cr.response_headers.append(item)
                    elif item.param_type == 'cookie': cr.response_cookies.append(item)
                    else: print(f"Warning: Invalid outgoing item '{item_str}' in complex req '{req_name}' (line {line_num+1})")
                else: print(f"Warning: Invalid outgoing item '{item_str}' in complex req '{req_name}' (line {line_num+1})")

            dsl_file.complex_requirements[req_name] = cr


        elif line.startswith("api tag"): # api tag <tag_name> [req/<req_name>...] [cite: 12, 21]
            if current_tag:
                dsl_file.tags.append(current_tag)
            
            parts = line.split()
            tag_name = parts[2]
            
            # Check for "mod" as tag name [cite: 61]
            if tag_name == "mod":
                print(f"Error: Cannot use 'mod' for API tag name (line {line_num+1}). Skipping tag.")
                current_tag = None # invalidate current tag context
                continue

            current_tag = DslTag(name=tag_name, py_module_name=f"{tag_name.lower()}.py")
            
            tag_req_names = []
            for part in parts[3:]:
                if part.startswith("req/"):
                    tag_req_names.append(part.split('/',1)[1])
            current_tag.complex_req_names = tag_req_names

        elif line.startswith("api"): # api[/hidden] [req/<req_name>...] <def...> [cite: 13]
            if current_tag:
                endpoint = parse_api_endpoint_line(line, dsl_file.type_definitions)
                if endpoint:
                    current_tag.endpoints.append(endpoint)
            else:
                print(f"Warning: API endpoint defined outside of a tag at line {line_num+1}: {line}")
        
        else: # [cite: 6] only lines starting with type, req, api are processed.
            print(f"Info: Skipping line {line_num+1} (doesn't start with type, req, or api): {line}")


    if current_tag: # Add the last parsed tag
        dsl_file.tags.append(current_tag)
        
    # --- Post-parsing processing: Resolve types and requirements ---
    # 1. Process Type Definitions
    pydantic_defs = ["from pydantic import BaseModel", "from typing import List, Dict, Optional, Any", "\n"]
    type_aliases = ["from typing import List, Dict, Optional, Any", "\n"]
    custom_imports = set()

    for type_name, type_def in dsl_file.type_definitions.items():
        model_code = generate_pydantic_model_for_dsl_type(type_def, dsl_file.type_definitions)
        if type_def.pydantic_model_def:
            pydantic_defs.append(type_def.pydantic_model_def)
        elif type_def.py_type_str and type_def.is_alias : # It's a type alias
             type_aliases.append(type_def.py_type_str)
        if type_def.py_import_stmt:
            custom_imports.add(type_def.py_import_stmt)
            
    dsl_file.pydantic_models_code = "\n".join(pydantic_defs)
    dsl_file.type_aliases_code = "\n".join(type_aliases)
    dsl_file.custom_imports_code = "\n".join(sorted(list(custom_imports)))


    # 2. Resolve Python types for all parameters and bodies
    all_entities_with_params = []
    for tag in dsl_file.tags:
        all_entities_with_params.extend(tag.endpoints)
    for req_name, req_def in dsl_file.complex_requirements.items():
        all_entities_with_params.append(req_def) # Complex reqs also have params

    for entity in all_entities_with_params:
        param_lists_to_update = []
        if hasattr(entity, 'path_params'): param_lists_to_update.append(entity.path_params)
        if hasattr(entity, 'query_params'): param_lists_to_update.append(entity.query_params)
        if hasattr(entity, 'header_params'): param_lists_to_update.append(entity.header_params)
        if hasattr(entity, 'cookie_params'): param_lists_to_update.append(entity.cookie_params)
        if hasattr(entity, 'form_params'): param_lists_to_update.append(entity.form_params)
        if hasattr(entity, 'response_headers'): param_lists_to_update.append(entity.response_headers)
        if hasattr(entity, 'response_cookies'): param_lists_to_update.append(entity.response_cookies)

        for param_list in param_lists_to_update:
            for param in param_list:
                param.py_type = dsl_type_to_python_type_str(param.dsl_type, dsl_file.type_definitions)

        body_attributes_to_update = []
        if hasattr(entity, 'request_body') and entity.request_body: body_attributes_to_update.append('request_body')
        if hasattr(entity, 'response_body') and entity.response_body: body_attributes_to_update.append('response_body')
        
        for attr_name in body_attributes_to_update:
            body_obj = getattr(entity, attr_name)
            if body_obj and body_obj.dsl_type:
                body_obj.py_type = dsl_type_to_python_type_str(body_obj.dsl_type, dsl_file.type_definitions)


    # 3. Resolve/Unite requirements for each endpoint (like Rust's unite_requirements)
    for tag in dsl_file.tags:
        for endpoint in tag.endpoints:
            # Start with direct endpoint definitions
            endpoint.final_path_params.extend(endpoint.path_params)
            endpoint.final_query_params.extend(endpoint.query_params)
            endpoint.final_header_params.extend(endpoint.header_params)
            endpoint.final_cookie_params.extend(endpoint.cookie_params)
            endpoint.final_form_params.extend(endpoint.form_params)
            endpoint.final_request_body = endpoint.request_body
            
            endpoint.final_response_body = endpoint.response_body
            endpoint.final_response_headers.extend(endpoint.response_headers)
            endpoint.final_response_cookies.extend(endpoint.response_cookies)

            # Collect all complex requirement names (endpoint + tag level)
            all_req_names_for_endpoint = set(endpoint.complex_req_names)
            all_req_names_for_endpoint.update(tag.complex_req_names)

            for req_name in all_req_names_for_endpoint:
                if req_name in dsl_file.complex_requirements:
                    complex_req = dsl_file.complex_requirements[req_name]
                    
                    # Add incoming from complex_req (headers, queries, cookies)
                    # Ensure no duplicates by name and type (simplification: just extend for now)
                    endpoint.final_header_params.extend(h for h in complex_req.header_params if not any(e_h.name == h.name for e_h in endpoint.final_header_params))
                    endpoint.final_query_params.extend(q for q in complex_req.query_params if not any(e_q.name == q.name for e_q in endpoint.final_query_params))
                    endpoint.final_cookie_params.extend(c for c in complex_req.cookie_params if not any(e_c.name == c.name for e_c in endpoint.final_cookie_params))
                    
                    # Add outgoing from complex_req (headers, cookies)
                    endpoint.final_response_headers.extend(h for h in complex_req.response_headers if not any(e_h.name == h.name for e_h in endpoint.final_response_headers))
                    endpoint.final_response_cookies.extend(c for c in complex_req.response_cookies if not any(e_c.name == c.name for e_c in endpoint.final_response_cookies))
                    
                    # Mark params from hidden complex reqs as hidden for OpenAPI if the req itself is hidden [cite: 24, 187]
                    if complex_req.is_hidden_openapi:
                        for param_list_name in ['final_header_params', 'final_query_params', 'final_cookie_params', 'final_response_headers', 'final_response_cookies']:
                            param_list_attr = getattr(endpoint, param_list_name)
                            for param_in_list in param_list_attr:
                                # Check if this param originated from *this* hidden complex_req
                                # This check is tricky. For now, if any complex req is hidden, its params could be considered hidden.
                                # Rust logic: `header.hidden = true` if from complex req. [cite: 187]
                                # We will set `param.is_hidden = complex_req.is_hidden_openapi` when copying.
                                # (This logic needs refinement to trace param origin for accurate hiding)
                                pass # This needs better tracking or a simpler rule.

    return dsl_file


# --- Code Generation (Normally in a separate codegen.py) ---

def generate_fastapi_param_string(param: DslParameter, for_openapi_spec: bool = False) -> str:
    """Generates a FastAPI function parameter string e.g., 'user_id: int = Query(...)' """
    py_type = param.py_type or "Any"
    param_name_py = param.name.replace('-', '_') # Python compatible variable name
    
    default_val_str = "..." # Ellipsis for required
    # For OpenAPI, can add description, examples etc.
    desc = f'description="{param.name} {param.param_type}"'
    
    # Hidden params for OpenAPI [cite: 19, 24]
    # In FastAPI, path operation parameters have `include_in_schema`
    # For Header, Query, Cookie, Path, this is controlled by the parameter itself.
    # `include_in_schema=False` if param.is_hidden or (for_openapi_spec and param.is_hidden_openapi)

    include_in_schema_str = ""
    if param.is_hidden : # For hidden complex requirements not meant for function params
        include_in_schema_str = ", include_in_schema=False"


    if param.param_type == 'path':
        return f"{param_name_py}: {py_type}" # Path params are part of signature directly
    if param.param_type == 'query':
        return f"{param_name_py}: {py_type} = Query({default_val_str}, {desc}{include_in_schema_str})"
    if param.param_type == 'header':
        # FastAPI Header uses 'alias' for the actual header name if different from var name
        alias_str = f', alias="{param.name}"' if param.name != param_name_py else ""
        return f"{param_name_py}: Optional[{py_type}] = Header(None, {desc}{alias_str}{include_in_schema_str})" # Default to Optional for headers
    if param.param_type == 'cookie':
        return f"{param_name_py}: Optional[{py_type}] = Cookie(None, {desc}{include_in_schema_str})"
    if param.param_type == 'form_param':
        return f"{param_name_py}: {py_type} = Form({default_val_str}, {desc}{include_in_schema_str})"
    return f"{param_name_py}: {py_type}" # Fallback

def generate_endpoint_func_code(endpoint: DslEndpoint, dsl_file: DslFile, tag_name:str) -> str:
    lines = []
    
    # Function signature
    func_params = []

    # Path parameters first
    for p_param in endpoint.final_path_params:
        func_params.append(f"{p_param.name.replace('-', '_')}: {p_param.py_type or 'Any'}")

    # Request Body (if any)
    # b/json/<type> -> body: PyType [cite: 8]
    # b/file/<key> -> key: UploadFile = File(...) [cite: 8]
    if endpoint.final_request_body:
        body = endpoint.final_request_body
        body_py_type = body.py_type or "Any"
        if body.body_type == 'json' or body.body_type == 'msgpack':
             func_params.append(f"payload: {body_py_type}") # FastAPI handles parsing from request
        elif body.body_type == 'file' and body.file_form_key:
             func_params.append(f"{body.file_form_key.replace('-','_')}: UploadFile = File(...)")

    # Query, Header, Cookie, Form parameters
    for q_param in endpoint.final_query_params: func_params.append(generate_fastapi_param_string(q_param))
    for h_param in endpoint.final_header_params: func_params.append(generate_fastapi_param_string(h_param))
    for c_param in endpoint.final_cookie_params: func_params.append(generate_fastapi_param_string(c_param))
    for f_param in endpoint.final_form_params: func_params.append(generate_fastapi_param_string(f_param))

    # Response model
    # -> b/json/Vec<ChatData> [cite: 14] means response_model=List[ChatData]
    response_model_str = "None"
    if endpoint.final_response_body and endpoint.final_response_body.py_type :
        if endpoint.final_response_body.body_type in ["json", "msgpack"]:
            response_model_str = endpoint.final_response_body.py_type
        elif endpoint.final_response_body.body_type == "plain":
            response_model_str = "str" # Handled by PlainTextResponse
        elif endpoint.final_response_body.body_type == "html":
            response_model_str = "str" # Handled by HTMLResponse
        # 'ok' and 'file' don't typically use response_model in the decorator in the same way.
        
    # Normalize tag name for OpenAPI
    openapi_tag_name = tag_name.replace('_', ' ').replace('-', ' ').title()

    decorator_params = [f'"{endpoint.path_template}"']
    if response_model_str != "None" and endpoint.final_response_body.body_type not in ['file', 'plain', 'html', 'ok']:
         decorator_params.append(f"response_model={response_model_str}")
    decorator_params.append(f'tags=["{openapi_tag_name}"]')
    if endpoint.is_hidden_openapi: # [cite: 19]
        decorator_params.append("include_in_schema=False")
    
    lines.append(f"@router.{endpoint.http_method}({', '.join(decorator_params)})")
    lines.append(f"async def {endpoint.func_name}({', '.join(func_params)}):")
    
    # Function body (placeholder like todo!(); [cite: 16, 17])
    # Extract data (Rust examples show this) [cite: 15, 18]
    # FastAPI does this automatically for annotated params.
    
    # Handle response headers/cookies [cite: 9]
    # For this, we might need to inject `response: Response` and use `response.set_cookie`, `response.headers[]`
    # This is more complex if mixing with `response_model`.
    # Alternative: return `JSONResponse(content=..., headers=..., cookies=...)`
    
    if endpoint.final_response_body:
        rb = endpoint.final_response_body
        if rb.body_type == "ok": # [cite: 8]
            lines.append("    return None # HTTP 200 OK or 204 No Content implicitly")
        elif rb.body_type == "plain":
            lines.append(f"    # TODO: return PlainTextResponse(content=...)")
            lines.append("    pass")
        elif rb.body_type == "html":
            lines.append(f"    # TODO: return HTMLResponse(content=...)")
            lines.append("    pass")
        elif rb.body_type == "file": # This implies returning a file path
            lines.append(f"    # TODO: return FileResponse(path='path/to/your/file')")
            lines.append("    pass")
        elif rb.body_type in ["json", "msgpack"]:
             lines.append(f"    # TODO: Implement logic and return data for {rb.py_type or 'response'}")
             lines.append("    pass")
        else:
            lines.append("    pass # TODO: Implement endpoint logic")
    else: # Should not happen based on parser [cite: 53]
        lines.append("    pass # TODO: Implement endpoint logic")

    return "\n".join(lines) + "\n"


def generate_tag_module_code(tag: DslTag, dsl_file: DslFile) -> str:
    # Initial imports
    code_lines = [
        "from fastapi import APIRouter, Query, Header, Cookie, File, Form, UploadFile, Depends, HTTPException, status, Response",
        "from fastapi.responses import PlainTextResponse, HTMLResponse, FileResponse",
        "from typing import List, Dict, Optional, Any",
        f"from ..models import * # Assuming generated types/models are in ..models.py or similar",
        "\nrouter = APIRouter()\n"
    ]

    for endpoint in tag.endpoints:
        code_lines.append(generate_endpoint_func_code(endpoint, dsl_file, tag.name))
        code_lines.append("\n")
    
    return "\n".join(code_lines)


def generate_main_app_code(dsl_file: DslFile, version: str) -> str:
    """Generates a main.py for the specific API version."""
    lines = [
        "from fastapi import FastAPI",
        "\napp = FastAPI(title=\"Generated API\", version=f\"{version}\")\n"
    ]
    for tag in dsl_file.tags:
        module_name = tag.py_module_name.replace(".py", "")
        lines.append(f"from .{module_name} import router as {module_name}_router")
        lines.append(f"app.include_router({module_name}_router, prefix='/api/{version}/{tag.name}')\n") # Example prefix
    
    lines.append("\n# To run: uvicorn main:app --reload (if this file is main.py in the version folder)")
    return "\n".join(lines)

def generate_models_file_code(dsl_file: DslFile) -> str:
    """Generates the content for the models.py file."""
    
    # Ensure BaseModel is imported if any Pydantic models are defined
    base_imports_needed = False
    if dsl_file.pydantic_models_code and "BaseModel" in dsl_file.pydantic_models_code :
        base_imports_needed = True
    
    header = []
    if base_imports_needed or dsl_file.type_aliases_code: # Basic typing imports
        header.append("from typing import List, Dict, Optional, Any")
    if base_imports_needed:
        header.append("from pydantic import BaseModel")

    # Add custom imports from type definitions
    if dsl_file.custom_imports_code:
        header.append(dsl_file.custom_imports_code)
    
    full_code = "\n".join(header) + "\n\n"
    
    if dsl_file.type_aliases_code.strip():
        # Remove the default "from typing import..." if already added
        aliases_code_cleaned = dsl_file.type_aliases_code.replace("from typing import List, Dict, Optional, Any\n\n","")
        full_code += aliases_code_cleaned.strip() + "\n\n"
        
    if dsl_file.pydantic_models_code.strip():
        # Remove the default imports if already added
        pydantic_code_cleaned = dsl_file.pydantic_models_code.replace("from pydantic import BaseModel\n","").replace("from typing import List, Dict, Optional, Any\n\n","")
        full_code += pydantic_code_cleaned.strip() + "\n\n"
        
    return full_code.strip()


# --- Main Script Logic ---
def main():
    # CLI arguments
    parser = argparse.ArgumentParser(description="DSL to FastAPI Translator")
    parser.add_argument("-i", "--input", required=True, help="Input DSL file")
    parser.add_argument("-o", "--output", required=True, help="Output folder for generated code")
    parser.add_argument("-v", "--version", help="API version (e.g., v1). Auto-increments if not set.")
    parser.add_argument("-r", "--regenerate", action="store_true", help="Don't bump version, rewrite all files.")
    args = parser.parse_args()

    input_file = Path(args.input)
    output_dir = Path(args.output)
    
    if not input_file.exists():
        print(f"Error: Input file '{input_file}' not found.")
        return

    dsl_content = input_file.read_text()
    parsed_dsl = parse_dsl_file_content(dsl_content) # [cite: 31]

    # Version decision logic (simplified from Rust)
    # For Python, we'll use provided version or default to "v1" and manage via folder.
    # A full breaking change detection is complex.
    api_version_str = args.version
    if not api_version_str:
        # Simplified versioning: find highest existing v_num and increment if not regenerating
        # Or, if regenerate is true and version exists, use that. Otherwise, find next.
        # For this example, let's default or use provided.
        api_version_str = "v1" 
        # A more robust version would be: `crate::entities::versions::decide_version` [cite: 32]
        # This involves reading .api.json from previous versions [cite: 35, 260]
        # and comparing with `parsed_dsl.no_breaking_changes(new_version)` logic
        
        # Basic auto-increment if not regenerating
        if not args.regenerate:
            current_max_v = 0
            for item in output_dir.iterdir():
                if item.is_dir() and item.name.startswith("v"):
                    try:
                        current_max_v = max(current_max_v, int(item.name[1:]))
                    except ValueError:
                        pass
            api_version_str = f"v{current_max_v + 1}"
        else: # Regenerating, find highest existing or default v1
             current_max_v = 0
             found = False
             for item in output_dir.iterdir():
                if item.is_dir() and item.name.startswith("v"):
                    found = True
                    try:
                        current_max_v = max(current_max_v, int(item.name[1:]))
                    except ValueError:
                        pass
             api_version_str = f"v{current_max_v}" if found and current_max_v > 0 else "v1"


    version_output_dir = output_dir / api_version_str
    version_output_dir.mkdir(parents=True, exist_ok=True) # [cite: 33]

    # Generate models/types file (e.g., models.py inside version_output_dir)
    models_code = generate_models_file_code(parsed_dsl)
    models_file_path = version_output_dir / "models.py" # Or types.py
    if not models_file_path.exists() or args.regenerate: # [cite: 34]
         models_file_path.write_text(models_code)
         print(f"Generated {models_file_path}")


    # Generate code for each tag [cite: 128]
    for tag in parsed_dsl.tags:
        module_code = generate_tag_module_code(tag, parsed_dsl)
        tag_file_path = version_output_dir / tag.py_module_name
        
        if not tag_file_path.exists() or args.regenerate: # [cite: 34]
            tag_file_path.write_text(module_code)
            print(f"Generated {tag_file_path}")

    # Generate main app file for the version [cite: 129] (like mod.rs or a main FastAPI app)
    main_app_code = generate_main_app_code(parsed_dsl, api_version_str)
    main_app_file_path = version_output_dir / "main_app.py" # Name it appropriately
    if not main_app_file_path.exists() or args.regenerate:
        main_app_file_path.write_text(main_app_code)
        print(f"Generated {main_app_file_path}")
        
    # Create __init__.py to make the folder a package
    init_py_path = version_output_dir / "__init__.py"
    if not init_py_path.exists() or args.regenerate:
        init_py_path.write_text("# FastAPI routes for version " + api_version_str + "\n")

    # Write .api.json (serialized DSL structure for versioning) [cite: 35]
    # This is useful for `no_breaking_changes` logic if implemented.
    api_json_path = version_output_dir / ".api.json"
    if not api_json_path.exists() or args.regenerate:
        try:
            api_json_path.write_text(parsed_dsl.model_dump_json(indent=2))
            print(f"Generated {api_json_path}")
        except Exception as e:
            print(f"Could not serialize DSL to JSON: {e}")


    print(f"FastAPI code generated in {version_output_dir}") # [cite: 36]

if __name__ == "__main__":
    main()
