#! /usr/bin/env python3.12

from mastodon import Mastodon
from atproto import Client, models

from PIL import Image
from urllib.parse import urlparse
import re
from typing import List, Dict
from crossref.restful import Works
import math
import textwrap
import config
import json
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import datetime, timezone
from ln_oauth import auth, headers
from math import sqrt
import html
import io
import doi
import pdfplumber
from pikepdf import Pdf
from pdftitle import get_title_from_io as getPDFtitle
from bs4 import BeautifulSoup
import mimetypes
#from sumy.parsers.html import HtmlParser
#from sumy.parsers.plaintext import PlaintextParser
#from sumy.nlp.tokenizers import Tokenizer
#from sumy.summarizers.kl import KLSummarizer as Summarizer2
#from sumy.summarizers.lsa import LsaSummarizer as Summarizer
#from sumy.nlp.stemmers import Stemmer
#from sumy.utils import get_stop_words
import cloudscraper
from os import path

LANGUAGE = "english"

mastodon = Mastodon(
    access_token='pytooter_usercred.secret',
    api_base_url='https://fediscience.org'
)


def match_last(orig_string, re_prefix, re_suffix):

    # first use positive-lookahead for the regex suffix
    re_lookahead= re.compile(f"{re_prefix}(?={re_suffix})")

    match= None
    # then keep the last match
    for match in re_lookahead.finditer(orig_string):
        pass

    if match:
        # now we return the proper match

        # first compile the proper regex…
        re_complete= re.compile(re_prefix + re_suffix)

        # …because the known start offset of the last match
        # can be supplied to re_complete.match
        return re_complete.match(orig_string, match.start())

    return match

def _get_title(soup):
    """
    Extracting the title of the page
    We take the text of the title tag or the first h1 tag
    """
    if (soup.title and soup.title.string != ''):
        return soup.title.string
    if soup.find("meta", property="og:title"):
        return soup.find("meta", property="og:title")["content"]
    if (soup.h1 and soup.h1.string != ''):
        return soup.h1.string
    return ''


def _get_description(soup):
    """
    Description of the page, the text following the h1 tag
    or just the first p tag
    """
    if soup.find("meta", property="og:description"):
        return soup.find("meta", property="og:description")["content"]
    first_h1 = soup.find('h1')
    if first_h1:
        first_p = first_h1.find_next('p')
        if (first_p and first_p.string != ''):
            return first_p.string
    first_h2 = soup.find('h2')
    if first_h2:
        first_p = first_h2.find_next('p')
        if (first_p and first_p.string != ''):
            return first_p.string
    first_p = soup.find('p')
    if (first_p and first_p.string != ''):
        return first_p.string        
    return ''


def getSummarytext(url):
    parser = PlaintextParser.from_string(url, Tokenizer(LANGUAGE))
    stemmer = Stemmer(LANGUAGE)
    summ = Summarizer(stemmer)
    summ2 = Summarizer2(stemmer)
    summ.stop_words = get_stop_words(LANGUAGE)
    summ2.stop_words = get_stop_words(LANGUAGE)
    fullText = ""
    sc = len(parser.document.sentences)
    if sc < 6:
        return ""
    if sc > 150:
      print("Shorten the FullText Stage 1:", sc, "\n")
      for sentence in summ(parser.document, max(150, int(150+sqrt(sc-150)))):
        if len(str(sentence)) > 40:
          fullText += str(sentence) + " "
      fullText = re.sub(r"\([^()]*\)", "", fullText)
      parser = PlaintextParser.from_string(fullText, Tokenizer(LANGUAGE))
      sc = len(parser.document.sentences)
      print("Shorten the FullText Stage 2:", sc, "\n")
      fullText = ""
    pc = len(parser.document.paragraphs)
    nos = min(max(3, int(0.01*sc), int(0.05*pc)), 5)
    for sentence in summ2(parser.document, nos):
      if len(str(sentence)) > 40:
        fullText += str(sentence) + " "
    fullText = re.sub(r"\([^()]*\)", "", fullText)
    while len(fullText) > 1000:
      nos=nos - 1
      if nos == 0:
        break
      fullText=""
      for sentence in summ2(parser.document, nos):
        fullText += str(sentence) + " "
      fullText = re.sub(r"\([^()]*\)", "", fullText)
    if len(fullText) > 1000:
       fullText = fullText.ljust(1000)[:1000] + u"\u2026"
    return fullText


def guess_type_of(link, strict=True):
    link_type, _ = mimetypes.guess_type(link)
    if link_type is None and strict:
      try:
        u = requests.get(link, verify=False, timeout=10)
        link_type = u.headers['Content-Type']
      except:
        print("URL does not exist on WWW")
        link_type = ""
    return link_type


def unshorten_url(url):
    try:
        url = requests.head(url, allow_redirects=True, verify=False, timeout=10).url
    except:
        print("Error with unshortening url\n")  
    return url

# Add the user Id you want to get tweets
user_id="109276496085205920"
# Add the number of tweets you want to get
number_of_tweets=50
since_id=109332416759887654
#since_id=109300309877736194

try:
    ltfile=open('lasttoot_bluesky.txt', "r")
    since_id=ltfile.readline() 
    since_id=int(since_id) + 1
    ltfile.close()
except:
    print("File does not exist\n")


print(since_id,"\n")

cred = mastodon.me()
timeline = mastodon.account_statuses(id=cred.id, only_media=False, pinned=False, exclude_replies=True, exclude_reblogs=False, min_id=since_id, limit=50)

# for tweet in timeline:
tweet=list(timeline)[-1]
log_id = tweet.id

#print(tweet,"\n")

org_name = ""

if (tweet.reblog):
#  id = tweet.reblog.id
  tweet = tweet.reblog
  org_name = tweet.account.display_name
  org_sname = tweet.account.acct


# print(tweet.content, "\n")

soup1 = BeautifulSoup(tweet.content, 'html5lib')
for match in soup1.findAll('span'):
    match.unwrap()
for match in soup1.findAll('a'):
    match.unwrap()
# print(soup1,"\n")    
soup1= BeautifulSoup(str(soup1), 'html5lib')
soup1= soup1.get_text("\n\n")



message = str(soup1).replace(" ,", ",").replace("  ", " ")
message=(message) + "\n\nvia " + str(tweet.url) + " "
#pattern=r'(?i)\b((?:[a-z][\w-]+:(?:/{1,3}|[a-z0-9%])|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:\'".,<>?«»“”‘’]))';
#match = re.findall(pattern, message)
#for m in match:
#    url = m[0]
#    message = message.replace(url, unshorten_url(url))
if len(org_name) > 1:
    org_name = re.sub(r':.+?:','', org_name)
#  message = "via " + org_name + " (" + '\u0040' + org_sname + ")\n" + message
    message = "via " + org_name.strip() + ":\n" + message

print("Text: ", message, "\n", len(message), "\n")


mediaurl=None
urldesc=None
try:
  for media in tweet.media_attachments:
    if media['remote_url'] != None:
      mediaurl= media['remote_url']
    else:
      mediaurl= media['url']
    if not urldesc:
      urldesc = media['description']
except:
  print("mediaurl error\n")


print(mediaurl,"\n")
print(urldesc,"\n")


### Bluesky
## https://www.bentasker.co.uk/posts/blog/software-development/automatically-posting-into-bsky-threads-and-nostr-from-python.html
## https://github.com/GanWeaving/social-cross-post/blob/main/bluesky.py

BLUESKY_EMAIL = 'dd-bluesky@dr-dittrich.de'
BLUESKY_PASSWORD ='t5y4-ip2y-j5ho-4izf'


URL_PATTERN = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

client = Client()

#

def parse_mentions(text: str) -> List[Dict]:
    spans = []
    # regex based on: https://atproto.com/specs/handle#handle-identifier-syntax
    mention_regex = rb"[$|\W](@([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
    text_bytes = text.encode("UTF-8")
    for m in re.finditer(mention_regex, text_bytes):
        spans.append({
            "start": m.start(1),
            "end": m.end(1),
            "handle": m.group(1)[1:].decode("UTF-8")
        })
    return spans

def parse_urls(text: str) -> List[Dict]:
    spans = []
    # partial/naive URL regex based on: https://stackoverflow.com/a/3809435
    # tweaked to disallow some training punctuation
    url_regex = rb"(https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*[-a-zA-Z0-9@%_\+~#//=])?)"
    text_bytes = text.encode("UTF-8")
    for m in re.finditer(url_regex, text_bytes):
        spans.append({
            "start": m.start(1),
            "end": m.end(1),
            "url": m.group(1).decode("UTF-8"),
        })
    return spans
    
# Parse facets from text and resolve the handles to DIDs
def parse_facets(text: str) -> List[Dict]:
    facets = []
    for m in parse_mentions(text):
        resp = requests.get(
            "https://bsky.social/xrpc/com.atproto.identity.resolveHandle",
            params={"handle": m["handle"]},
        )
        # If the handle can't be resolved, just skip it!
        # It will be rendered as text in the post instead of a link
        if resp.status_code == 400:
            continue
        did = resp.json()["did"]
        facets.append({
            "index": {
                "byteStart": m["start"],
                "byteEnd": m["end"],
            },
            "features": [{"$type": "app.bsky.richtext.facet#mention", "did": did}],
        })
    for u in parse_urls(text):
        facets.append({
            "index": {
                "byteStart": u["start"],
                "byteEnd": u["end"],
            },
            "features": [
                {
                    "$type": "app.bsky.richtext.facet#link",
                    # NOTE: URI ("I") not URL ("L")
                    "uri": u["url"],
                }
            ],
        })
    return facets

def fetch_embed_url_card(url: str) -> Dict:
  # the required fields for an embed card
  card = {
      "uri": url,
      "title": "",
      "description": "",
  }


  url = unshorten_url(url)
  if bool(re.search(r"repec",url)):
    try:
      url = unshorten_url(url)
      urldesc = url
    except:
      print("RePec Error\n")

  if bool(re.search(r"doi.org",url)):
    try:
      udoi =  re.sub(r'https://doi.org/', '', url) 
      url = doi.get_real_url_from_doi(udoi)
      works = Works()
      works.doi(udoi)
      print(works, "\n")
      exit()
    except:
      print("DOI Error 1\n")

  ltype = guess_type_of(url)
  if 'text/html' in ltype:
#    url = unshorten_url(url)
#    scraper =  cloudscraper.create_scraper()
    try:
      reqs = requests.get(url, verify=False, timeout=10)
#      reqs = scraper.get(url)
#    except:
#      reqs = requests.get(url, verify=False, timeout=10)
    # using the BeaitifulSoup module
#    try:
      soup = BeautifulSoup(reqs.text, 'lxml')
    except:
      print("Problems parsing html\n")
    title_tag = soup.find("meta", property="og:title")
    if title_tag:
      card["title"] = title_tag["content"]
    else:
      try:  
       card["title"] = html.unescape(_get_title(soup))
      except:
        print("No title found\n")

    description_tag = soup.find("meta", property="og:description")
    if description_tag: 
      card["description"] = description_tag["content"]
    else:
      card["description"] = html.unescape(_get_description(soup)) if _get_description(soup) else ""
      

    image_tag = soup.find("meta", property="og:image")
    if image_tag:
      img_url = image_tag["content"]
      if "://" not in img_url:
          img_url = url + img_url
      resp = requests.get(img_url, verify=False, timeout=10)
      if resp.status_code == requests.codes.ok:  # image returned OK
        try:
          f = io.BytesIO(resp.content)
          img = Image.open(f).convert('RGB')
          if img is None:
            print("Error: thumb 1")
          w, h = img.size  
          print(img_url, "\nbefore: ", w, ":", h, "\n")
          img.thumbnail((400, 400), resample=Image.Resampling.LANCZOS, reducing_gap=3.0) 
          w, h = img.size  
          print("resized: ", w, ":", h, "\n")
          if img is None:
            print("Error: thumb 3")
          im_out = io.BytesIO()
          img.save(im_out, format="JPEG", optimize=True)
          upload = client.com.atproto.repo.upload_blob(im_out.getvalue())
          f.close()
          card["thumb"] = upload.blob
        except Exception as e:
          fout = open('mastodon2bluesky.log', 'a')
          outj = f"Upload of thumb failed: {img_url} : {e}"
          print(outj, "\n")
          out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(outj), str(tweet.id), "\n"])
          fout.write(out)
          fout.close()
#          exit()

  elif bool(re.search(r"pdf",url)):
    if 'pdf' in ltype:
      try:
        scraper =  cloudscraper.create_scraper()
        reqs = scraper.get(url)
      except:
        reqs = requests.get(url, verify=False, timeout=10)   
      try:
        f = io.BytesIO(reqs.content)
        pdf = Pdf.open(f)
        meta = pdf.open_metadata()
      except:
        print("cannot get pdf\n")
      title = None
      description = None
      try:
        title = meta['dc:title']
        card["title"] = title
        description = meta['dc:description']
        card["description"] = description
      except:
        print("metadata error")
      if not title:
        try:
          title = getPDFtitle(f)
          card["title"] = title
        except:
          print("title error")
      if not title:
        card["title"] = "PDF"
      if not description: 
        card["description"] = ""

  if bool(re.search(r"doi",url)):
    try:
      udoi =  re.findall(r'\b10\.\d{4,9}/[-.;()/:\w]+', url)[0]
      print(udoi, "\n")      
      works = Works()
      out = works.doi(udoi)
      card["title"] = out["title"][0] if out["title"][0] else card["title"]
      card["description"] = BeautifulSoup(out["abstract"], 'lxml').getText().strip() if out["abstract"] else card["description"]
    except:
      print("DOI Error 2\n")

  #print(card,"\n")

  return {
        "$type": "app.bsky.embed.external",
        "external": card,
    }



# Bluesky
def login_to_bluesky():
    global client
    try:
        client.login(BLUESKY_EMAIL, BLUESKY_PASSWORD)
        #print("Successfully logged in to Bluesky.")
    except Exception as e:
        fout = open('mastodon2bluesky.log', 'a')
        outj = f"Failed to log in to Bluesky: {e}"
        out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(outj), str(tweet.id), "\n"])
        fout.write(out)
        fout.close()
        

def post_to_bluesky(text, image_locations, alt_texts):
    try:
        login_to_bluesky()
    except Exception as e:
        fout = open('mastodon2bluesky.log', 'a')
        outj = f"Failed to log in to Bluesky: {e}"
        out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(outj), str(tweet.id), "\n"])
        fout.write(out)
        fout.close()      
        return False

  
    images = []
    for idx, image_location in enumerate(image_locations):
        try:
            print(image_location, "\n")
            r_pic = requests.get(image_location, verify=False, timeout=10)
            f_type = r_pic.headers['Content-Type']
            f_name = path.basename(image_location)
            if r_pic.status_code == requests.codes.ok:  # image returned OK
              try:
                f = io.BytesIO(r_pic.content)
                upload = client.com.atproto.repo.upload_blob(f)
                images.append(models.AppBskyEmbedImages.Image(alt=alt_texts[idx], image=upload.blob))
                f.close()
              except Exception as e:
                print(e,"\n")
        except Exception as e:
            # Exception handling: log the error and local file path
            fout = open('mastodon2bluesky.log', 'a')
            outj = f"Unable to process the image file at {f_name} for Bluesky. Error: {e}"
            out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(outj), str(tweet.id), "\n"])
            fout.write(out)
            fout.close()      
            return False

    facets = parse_facets(text) if URL_PATTERN.search(text) else None
    if images:
      embed = models.AppBskyEmbedImages.Main(images=images) 
    else:
      urls = None
      urls = re.search('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', str(text))
      if urls:
        urls = None if bool(re.search('fediscience.org/@davdittrich', urls.group(0))) else urls
      embed = fetch_embed_url_card(urls.group(0)) if urls else None

    try:
        r = client.com.atproto.repo.create_record(
            models.ComAtprotoRepoCreateRecord.Data(
                repo=client.me.did,
                collection='app.bsky.feed.post',
                record=models.AppBskyFeedPost.Record(
                    createdAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), text=text, embed=embed, facets=facets
                ),
            )
        )
#        logger.debug("Bluesky post created.")
    except Exception as e:
        fout = open('mastodon2bluesky.log', 'a')
        outj = f"Failed to create Bluesky post: {e}"
        out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(outj), str(tweet.id), "\n"])
        fout.write(out)
        fout.close()      
        return False

    return r

##
def reply_to_bluesky(text, reply):
    try:
        login_to_bluesky()
    except Exception as e:
        fout = open('mastodon2bluesky.log', 'a')
        outj = f"Failed to log in to Bluesky: {e}"
        out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(outj), str(tweet.id), "\n"])
        fout.write(out)
        fout.close()      
        return False
    
    facets = parse_facets(text) if URL_PATTERN.search(text) else None
    urls = None
    urls = re.search('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', str(text))
    if urls:
      urls = None if bool(re.search('fediscience.org/@davdittrich', urls.group(0))) else urls
    embed = fetch_embed_url_card(urls.group(0)) if urls else None

    try:
        r = client.com.atproto.repo.create_record(
            models.ComAtprotoRepoCreateRecord.Data(
                repo=client.me.did,
                collection='app.bsky.feed.post',
                record=models.AppBskyFeedPost.Record(
                    createdAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), text=text, embed=embed, facets=facets, reply=reply
                ),
            )
        )
#        logger.debug("Bluesky post created.")
    except Exception as e:
        fout = open('mastodon2bluesky.log', 'a')
        outj = f"Failed to create Bluesky post: {e}"
        out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(outj), str(tweet.id), "\n"])
        fout.write(out)
        fout.close()      
        return False

    return r
##


###
# message
# obtain length of tweet 

tweet_length = len(message)

# check length
if tweet_length < 290:
  tweet_length_limit = 290

elif tweet_length >= 290:
  # divided tweet_length / 280
  # You might consider adjusting this down 
  # depending on how you want to format the 
  # tweet.

  if re.search(r'\S\.\S', message):
    if re.search(r'http', message):
      tweet_length_limit = 290
    else:
      tweet_length_limit = 285
  else:
    tweet_length_limit = 290

# determine the number of tweets 
tweet_chunk_length = tweet_length_limit
tweet_count = math.ceil(tweet_length / tweet_chunk_length)

# chunk the tweet into individual pieces
tweet_chunks = textwrap.wrap(message,  tweet_chunk_length, break_long_words=False, break_on_hyphens=False, replace_whitespace=False )

# iterate over the chunks 
res_root = None
res_parent = None
x = 1
for chunk in (tweet_chunks):
  if x == 1:
    if tweet_count > 1:
      part = "" + chunk + " " + "… " + "".join([str(x), "/" ,str(tweet_count)])
    else:
      part = chunk
  
    part = part.strip() 
    print("Chunk ", x, ": ", len(part), "\n") 

    mediaurl = [mediaurl] if mediaurl else ''
    urldesc = [urldesc] if urldesc else ''
    r = post_to_bluesky(part, mediaurl, urldesc)
    mediaurl = False
    urldesc = False
    res_root = r
    res_parent = r
  else:
    if x == tweet_count:
      part = ''.join([chunk," ", str(x), "/" ,str(tweet_count)])
    else:
      part = ''.join([chunk," … ", str(x), "/" ,str(tweet_count)])
    
    part = part.strip() 
    print("Chunk ", x, ": ", len(part)) 

    parent = models.create_strong_ref(res_parent)
    root = models.create_strong_ref(res_root)
    reply=models.AppBskyFeedPost.ReplyRef(root=root, parent=parent)
    r = reply_to_bluesky(part, reply)
    res_parent = r

  print(part, "\n")
  x = x+1

###


fout = open('mastodon2bluesky.log', 'a')
out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), str(log_id), str(r), "\n"])
fout.write(out)
fout.close()
#except:
#  print("Logfile not written\n")
if r:
  ltfile = open('lasttoot_bluesky.txt','w')
  ltfile.write(str(log_id))
  ltfile.close()