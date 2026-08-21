# hAI.PortainerMCP Agent Instructions

This document provides instructions for agents interacting with the hAI.PortainerMCP server, which manages multiple Portainer instances via aliases.

## 1. Authentication

Agents must authenticate with the hAI.PortainerMCP server using a **Bearer API key**. This API key is configured per Portainer instance alias on the MCP server. When making requests to the MCP endpoint (`:8025/mcp`), include the API key in the `Authorization` header as a Bearer token.

**Example:**
`Authorization: Bearer YOUR_API_KEY`

## 2. Available MCP Tools

The following tools are available for agents to interact with Portainer instances through the MCP server:

*   **`portainer_alias_list`**: Lists all configured aliases (name, URL, has_key status) without exposing the actual API keys.
    *   **Purpose**: Discover available Portainer instances and their aliases.

*   **`portainer_alias_get(alias_name)`**: Retrieves details for a specific alias, including its URL and API key.
    *   **Purpose**: Get full connection details for a specific Portainer instance.

*   **`portainer_alias_add(alias_name, url, api_key)`**: Adds a new Portainer instance alias.
    *   **Purpose**: Register a new Portainer instance with the MCP server.

*   **`portainer_alias_update(alias_name, url, api_key)`**: Updates the URL or API key for an existing Portainer alias.
    *   **Purpose**: Modify connection details for an existing Portainer instance.

*   **`portainer_alias_remove(alias_name)`**: Removes a Portainer alias.
    *   **Purpose**: Deregister a Portainer instance from the MCP server.

*   **`portainer_status(alias_name)`**: Checks the reachability and version of a specific Portainer instance.
    *   **Purpose**: Verify the operational status of a Portainer instance.

*   **`portainer_endpoints(alias_name)`**: Lists all environments/endpoints configured within a specific Portainer instance.
    *   **Purpose**: Enumerate environments managed by a Portainer instance.

*   **`portainer_containers_list(alias_name, endpoint_id)`**: Lists containers for a given endpoint in a Portainer instance.
    *   **Purpose**: View running containers in a specific environment.

*   **`portainer_stacks_list(alias_name, endpoint_id)`**: Lists stacks for a given endpoint in a Portainer instance.
    *   **Purpose**: Inspect deployed application stacks in an environment.

## 3. General Usage Guidelines

*   **Alias-centric Interaction**: Always refer to Portainer instances by their configured `alias_name` when using the MCP tools.
*   **Error Handling**: Agents should be prepared to handle various responses, including authentication failures, network issues, and invalid input for tool parameters.
*   **Security**: Treat `api_key` values as sensitive information. Avoid logging or exposing them in unencrypted storage.
