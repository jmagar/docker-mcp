#!/bin/bash

# Create file to store all unresolved items
echo "" > /tmp/all_unresolved_items.jsonl

# Function to get page of review threads
get_page() {
    local cursor=$1
    local after_clause=""
    if [ -n "$cursor" ]; then
        after_clause=", after: \"$cursor\""
    fi
    
    gh api graphql -f query="
    {
      repository(owner: \"jmagar\", name: \"docker-mcp\") {
        pullRequest(number: 12) {
          reviewThreads(first: 100$after_clause) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              isResolved
              comments(first: 1) {
                nodes {
                  body
                  path
                  line
                  originalLine
                  createdAt
                  author {
                    login
                  }
                  url
                }
              }
            }
          }
        }
      }
    }"
}

cursor=""
page_count=0

while true; do
    echo "Processing page $((page_count + 1))..."
    
    response=$(get_page "$cursor")
    
    # Extract unresolved items and append to file
    echo "$response" | jq -r '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false) | @json' >> /tmp/all_unresolved_items.jsonl
    
    # Check if there's a next page
    has_next=$(echo "$response" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
    
    if [ "$has_next" = "false" ]; then
        break
    fi
    
    # Get cursor for next page
    cursor=$(echo "$response" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
    page_count=$((page_count + 1))
    
    # Safety check
    if [ $page_count -gt 10 ]; then
        echo "Stopping after 10 pages for safety"
        break
    fi
done

echo "Completed processing $((page_count + 1)) pages"
wc -l /tmp/all_unresolved_items.jsonl
