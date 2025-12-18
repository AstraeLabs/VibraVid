# 20.04.2024

import os
import json
from datetime import datetime
from urllib.parse import urlparse


# External libraries
import httpx
import tldextract
import ua_generator
import dns.resolver


# Variables
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE_PATH = os.path.join(SCRIPT_DIR, "domains.json")
ua =  ua_generator.generate(device='desktop', browser=('chrome', 'edge'))


def get_headers():
    return ua.headers.get()

def log(msg, level='INFO'):
    levels = {
        'INFO': '[ ]',
        'SUCCESS': '[+]',
        'WARNING': '[!]',
        'ERROR': '[-]'
    }
    entry = f"{levels.get(level, '[?]')} {msg}"
    print(entry)

def load_json_data(file_path):
    if not os.path.exists(file_path):
        log(f"Error: The file {file_path} was not found.", "ERROR")
        return None
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
        
    except Exception as e:
        log(f"Error reading the file {file_path}: {e}", "ERROR")
        return None

def save_json_data(file_path, data):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log(f"Data successfully saved to {file_path}", "SUCCESS")

    except Exception as e:
        log(f"Error saving the file {file_path}: {e}", "ERROR")

def parse_url(url):
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        extracted = tldextract.extract(url)
        parsed = urlparse(url)

        # Mantieni il netloc originale (con www se presente)
        clean_url = f"{parsed.scheme}://{parsed.netloc}/"
        full_domain = parsed.netloc
        domain_tld = extracted.suffix
        result = {
            'url': clean_url,
            'full_domain': full_domain,
            'domain': domain_tld,
            'suffix': extracted.suffix,
            'subdomain': extracted.subdomain or None
        }
        return result
    
    except Exception as e:
        log(f"Error parsing URL: {e}", "ERROR")
        return None

def check_dns_resolution(domain):
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = 2
        resolver.lifetime = 2

        try:
            answers = resolver.resolve(domain, 'A')
            return str(answers[0])
        except Exception:
            try:
                answers = resolver.resolve(domain, 'AAAA')
                return str(answers[0])
            
            except Exception:
                pass
        return None
    
    except Exception:
        return None

def find_new_domain(input_url, output_file=None, verbose=True, json_output=False):
    log_buffer = []
    original_info = parse_url(input_url)

    if not original_info:
        log(f"Could not parse original URL: {input_url}", "ERROR")
        if json_output:
            return {'full_url': input_url, 'domain': None}
        return None

    log(f"Starting analysis for: {original_info['full_domain']}")
    orig_ip = check_dns_resolution(original_info['full_domain'])
    if orig_ip:
        log(f"Original domain resolves to: {orig_ip}", "SUCCESS")
    else:
        log("Original domain does not resolve to an IP address", "WARNING")
    
    headers = get_headers()
    new_domains = []
    redirects = []
    final_url = None
    final_domain_info = None
    url_to_test_in_loop = None

    for protocol in ['https://', 'http://']:
        try:
            url_to_test_in_loop = f"{protocol}{original_info['full_domain']}"
            log(f"Testing connectivity to {url_to_test_in_loop}")
            redirect_chain = []
            current_url = url_to_test_in_loop
            max_redirects = 10
            redirect_count = 0

            while redirect_count < max_redirects:
                with httpx.Client(verify=False, follow_redirects=False, timeout=5) as client:
                    response = client.get(current_url, headers=headers)

                redirect_info = {'url': current_url, 'status_code': response.status_code}
                redirect_chain.append(redirect_info)
                log(f"Request to {current_url} - Status: {response.status_code}")

                if response.status_code in (301, 302, 303, 307, 308):
                    if 'location' in response.headers:
                        next_url = response.headers['location']
                        if next_url.startswith('/'):
                            parsed_current = urlparse(current_url)
                            next_url = f"{parsed_current.scheme}://{parsed_current.netloc}{next_url}"

                        log(f"Redirect found: {next_url} (Status: {response.status_code})")
                        current_url = next_url
                        redirect_count += 1
                        redirect_domain_info_val = parse_url(next_url)
                        if redirect_domain_info_val and redirect_domain_info_val['full_domain'] != original_info['full_domain']:
                            new_domains.append({'domain': redirect_domain_info_val['full_domain'], 'url': next_url, 'source': 'redirect'})

                    else:
                        log("Redirect status code but no Location header", "WARNING")
                        break
                else:
                    break

            if redirect_chain:
                final_url = redirect_chain[-1]['url']
                final_domain_info = parse_url(final_url)
                redirects.extend(redirect_chain)
                log(f"Final URL after redirects: {final_url}", "SUCCESS")
                if final_domain_info and final_domain_info['full_domain'] != original_info['full_domain']:
                    new_domains.append({'domain': final_domain_info['full_domain'], 'url': final_url, 'source': 'final_url'})

            final_status = redirect_chain[-1]['status_code'] if redirect_chain else None

            if final_status and final_status < 400 and final_status != 403:
                break

            if final_status == 403 and redirect_chain and len(redirect_chain) > 1:
                log(f"Got 403 Forbidden, but captured {len(redirect_chain)-1} redirects before that", "SUCCESS")
                break

        except httpx.RequestError as e:
            log(f"Error connecting to {protocol}{original_info['full_domain']}: {str(e)}", "ERROR")
    
    url_for_auto_redirect = input_url 
    if url_to_test_in_loop:
        url_for_auto_redirect = url_to_test_in_loop
    elif original_info and original_info.get('url'): 
        url_for_auto_redirect = original_info['url']

    if not redirects or not new_domains:
        log("Trying alternate method with automatic redirect following")

        try:
            with httpx.Client(verify=False, follow_redirects=True, timeout=5) as client:
                response_auto = client.get(url_for_auto_redirect, headers=headers)

            log(f"Connected with auto-redirects: Status {response_auto.status_code}")

            if response_auto.history:
                log(f"Found {len(response_auto.history)} redirects with auto-following", "SUCCESS")

                for r_hist in response_auto.history:
                    redirect_info_auto = {'url': str(r_hist.url), 'status_code': r_hist.status_code}
                    redirects.append(redirect_info_auto)
                    log(f"Auto-redirect: {r_hist.url} (Status: {r_hist.status_code})")

                final_url = str(response_auto.url)
                final_domain_info = parse_url(final_url)
                for redirect_hist_item in response_auto.history:
                    redirect_domain_val = parse_url(str(redirect_hist_item.url))
                    if redirect_domain_val and original_info and redirect_domain_val['full_domain'] != original_info['full_domain']:
                        new_domains.append({'domain': redirect_domain_val['full_domain'], 'url': str(redirect_hist_item.url), 'source': 'auto-redirect'})
            
            current_final_url_info = parse_url(str(response_auto.url))

            if current_final_url_info and original_info and current_final_url_info['full_domain'] != original_info['full_domain']:
                is_already_added = any(d['domain'] == current_final_url_info['full_domain'] and d['source'] == 'auto-redirect' for d in new_domains)
                if not is_already_added:
                    new_domains.append({'domain': current_final_url_info['full_domain'], 'url': str(response_auto.url), 'source': 'final_url_auto'})
                final_url = str(response_auto.url)
                final_domain_info = current_final_url_info
                log(f"Final URL from auto-redirect: {final_url}", "SUCCESS")

        except httpx.RequestError as e:
            log(f"Error with auto-redirect attempt: {str(e)}", "ERROR")
        except NameError: 
            log("Error: URL for auto-redirect attempt was not defined.", "ERROR")
    
    unique_domains = []
    seen_domains = set()
    for domain_info_item in new_domains:
        if domain_info_item['domain'] not in seen_domains:
            seen_domains.add(domain_info_item['domain'])
            unique_domains.append(domain_info_item)
    
    if not final_url:
        final_url = input_url
    if not final_domain_info:
        final_domain_info = original_info 
        
    if final_domain_info:
        parsed_final_url_info = parse_url(final_url) 
        if parsed_final_url_info:
            final_url = parsed_final_url_info['url']
            final_domain_info = parsed_final_url_info 
    else: 
        final_domain_info = original_info
        final_url = original_info['url'] if original_info else input_url

    # Force HTTPS if final URL is HTTP
    if final_url and final_url.startswith('http://'):
        final_url = final_url.replace('http://', 'https://', 1)

    results_original_domain = original_info['full_domain'] if original_info else None
    results_final_domain_tld = final_domain_info['domain'] if final_domain_info and 'domain' in final_domain_info else None

    results = {
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'original_url': input_url,
        'original_domain': results_original_domain,
        'original_ip': orig_ip,
        'new_domains': unique_domains,
        'redirects': redirects,
        'log': log_buffer
    }
    simplified_json_output = {'full_url': final_url, 'domain': results_final_domain_tld}
    
    if verbose:
        log(f"DEBUG - Simplified output: {simplified_json_output}", "INFO")
    
    if output_file:
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            log(f"Results saved to {output_file}", "SUCCESS")
        except Exception as e:
            log(f"Error writing to output file: {str(e)}", "ERROR")
    
    if json_output:
        return simplified_json_output
    else:
        return results

def update_site_entry(site_name: str, all_domains_data: dict):
    site_config = all_domains_data.get(site_name, {})
    log(f"Processing site: {site_name}", "INFO")
    if not site_config.get('full_url'):
        log(f"Site {site_name} has no full_url in config. Skipping.", "WARNING")
        return False

    current_full_url = site_config.get('full_url')
    current_domain_tld = site_config.get('domain')
    found_domain_info = find_new_domain(current_full_url, verbose=False, json_output=True)

    if found_domain_info and found_domain_info.get('full_url') and found_domain_info.get('domain'):
        new_full_url = found_domain_info['full_url']
        new_domain_tld = found_domain_info['domain']

        if new_full_url != current_full_url or new_domain_tld != current_domain_tld:
            log(f"Update found for {site_name}: URL '{current_full_url}' -> '{new_full_url}', TLD '{current_domain_tld}' -> '{new_domain_tld}'", "SUCCESS")
            updated_entry = site_config.copy()
            updated_entry['full_url'] = new_full_url
            updated_entry['domain'] = new_domain_tld
            if new_domain_tld != current_domain_tld :
                updated_entry['old_domain'] = current_domain_tld if current_domain_tld else ""

            updated_entry['time_change'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            all_domains_data[site_name] = updated_entry
            return True
        
        else:
            log(f"No changes detected for {site_name}.", "INFO")
            return False
    else:
        log(f"Could not reliably find new domain info for {site_name} from URL: {current_full_url}. No search fallback.", "WARNING")
        return False

def main():
    log("Starting domain update script...")
    all_domains_data = load_json_data(JSON_FILE_PATH)
    if not all_domains_data:
        log("Cannot proceed: Domain data is missing or could not be loaded.", "ERROR")
        log("Script finished.")
        return

    any_updates_made = False
    for site_name_key in list(all_domains_data.keys()):
        if update_site_entry(site_name_key, all_domains_data):
            any_updates_made = True
        print("\n")
    
    if any_updates_made:
        save_json_data(JSON_FILE_PATH, all_domains_data)
        log("Update complete. Some entries were modified.", "SUCCESS")
    else:
        log("Update complete. No domains were modified.", "INFO")
    log("Script finished.")

if __name__ == "__main__":
    main()
