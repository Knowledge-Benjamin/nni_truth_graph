import re

with open(r'c:\Users\TempAdmin\Desktop\nni_truth_graph\ai_engine\pipeline\4_extraction.py', 'r', encoding='utf-8') as f:
    content = f.read()

# We need to change how claims are stored.
# 1. Initialize `all_article_claims = []` before `for chunk_idx, chunk_text in enumerate(chunks):`
# 2. Inside the chunk loop, instead of connecting to DB, just do:
#        all_article_claims.extend([(chunk_idx, c) for c in claim_list.claims])
# 3. After the chunk loop, if not retryable_failure and not extraction_failed_permanently:
#        connect to DB and insert all claims

# Let's extract the DB insertion block.
start_marker = """                                    save_conn = psycopg2.connect(DATABASE_URL)
                                    save_conn.autocommit = False
                                    cursor = save_conn.cursor()"""

end_marker = """                                    # Commit after successful chunk
                                    save_conn.commit()
                                    cursor.close()
                                    save_conn.close()"""

db_block_start = content.find(start_marker)
db_block_end = content.find(end_marker) + len(end_marker)

if db_block_start != -1 and db_block_end != -1:
    original_db_block = content[db_block_start:db_block_end]
    
    # We replace the DB block inside the chunk loop with just storing the claims
    store_claims_logic = """                                    # Store claims in memory to commit transactionally later
                                    all_article_claims.extend([(chunk_idx, c) for c in claim_list.claims])
                                    
                                    try:
                                        claims_serializable = [c.dict() if hasattr(c, 'dict') else c for c in claim_list.claims]
                                        claims_preview = json.dumps(claims_serializable, ensure_ascii=False)
                                        max_preview = int(os.getenv('CLAIM_PREVIEW_MAX', '4000'))
                                        if len(claims_preview) > max_preview:
                                            claims_preview = claims_preview[:max_preview] + '...'
                                        print(f"      [CLAIMS PARSED] Article {article_id} Chunk {chunk_idx+1} Claims: {claims_preview}")
                                    except Exception as _e:
                                        pass"""
    
    new_content = content[:db_block_start] + store_claims_logic + content[db_block_end:]
    
    # Initialize all_article_claims before chunk loop
    init_marker = "                if True:\n                    for chunk_idx, chunk_text in enumerate(chunks):"
    new_init = "                all_article_claims = []\n" + init_marker
    new_content = new_content.replace(init_marker, new_init)
    
    # Now, insert the DB block AFTER the chunk loop
    after_chunk_loop_marker = "                # (save_conn is now closed per-chunk)"
    
    # Adjust indentation of original_db_block to match the new location
    # The original was indented at 36 spaces. The new location should be indented at 20 spaces.
    db_lines = original_db_block.split('\\n')
    unindented_db_lines = []
    for line in db_lines:
        if line.startswith(' ' * 16): # removing 16 spaces
            unindented_db_lines.append(line[16:])
        else:
            unindented_db_lines.append(line.lstrip())
    
    unindented_db_block = '\\n'.join(unindented_db_lines)
    
    # The DB block used `claim_list.claims`. We will use `all_article_claims` and unpack chunk_idx, claim.
    unindented_db_block = unindented_db_block.replace("for claim in claim_list.claims:", "for chunk_idx, claim in all_article_claims:")
    
    insertion_logic = f'''                if not retryable_failure and not extraction_failed_permanently and all_article_claims:
                    try:
{unindented_db_block}
                    except Exception as fatal_db_e:
                        print(f"      [FATAL DB ERROR] Transaction failed for article {{article_id}}: {{fatal_db_e}}")
                        extraction_failed_permanently = True

'''
    
    new_content = new_content.replace(after_chunk_loop_marker, insertion_logic + after_chunk_loop_marker)
    
    with open(r'c:\Users\TempAdmin\Desktop\nni_truth_graph\ai_engine\pipeline\4_extraction.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Successfully patched 4_extraction.py")
else:
    print("Could not find DB block markers")
