"""Fixed synthetic data; new isolated database per run, no existing index/data deletion.
Run: python -m scripts.benchmark_library_queries
"""
import json
import os
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

from pymongo import MongoClient
from pymongo.collection import Collection
from database.schema import initialize
from database.bank_schema import initialize_banks
from app.services.library_pages import LibraryPages
from app.repositories.library_statistics import LibraryStatisticsRepository
from app.domain.question_content import content_hash


def main():
    client=MongoClient(os.getenv('MONGODB_URI','mongodb://127.0.0.1:27021'),serverSelectionTimeoutMS=3000)
    db=client['survey_query_benchmark_'+uuid4().hex]
    initialize(db);initialize_banks(db)
    # Baseline differs only by the three indexes introduced in 0.11.0.
    for collection,name in [('surveys','owned_version_usage'),('question_templates','owned_question_page'),('question_banks','bank_owner_page')]:
        db[collection].drop_index(name)
    now=datetime(2026,10,29,tzinfo=timezone.utc);owner='benchmark-owner';tid='target';vid='version-1'
    content={'type':'text','title':'固定文本样本','required':True,'validation':{'min_length':1,'max_length':100}}
    versions=[{'_id':f'version-{i}','schema_version':1,'template_id':tid,'number':i,
        'parent_version_id':f'version-{i-1}' if i>1 else None,'derived_from_version_id':None,
        'restored_from_version_id':None,'content':content,'content_hash':content_hash(content),
        'created_at':now,'created_by':owner,'origin':{'kind':'library'}} for i in range(1,5001)]
    db.question_versions.insert_many(versions)
    templates=[{'_id':tid if i==0 else f'template-{i:05}','schema_version':1,'owner_id':owner,
        'latest_version_id':'version-5000','edit_version':5000,'visibility':'private','origin':{'kind':'library'},
        'created_at':now,'updated_at':now} for i in range(3000)]
    db.question_templates.insert_many(templates)
    surveys=[];responses=[];scopes=[]
    for i in range(2000):
        sid=f'survey-{i:05}';v=vid if i<1000 else 'version-2';who=owner if i<100 or i>=1000 else 'foreign'
        source={'instance_id':'q','template_id':tid,'template_version_id':v,'version_number':1 if v==vid else 2,
                'content_hash':content_hash(content),'modified':False}
        surveys.append({'_id':sid,'schema_version':1,'owner_id':who,'title':sid,'description':'','status':'published',
            'version':1,'published_version':1,'share_id':sid,'allow_anonymous':True,'allow_multiple':True,
            'deadline':None,'created_at':now,'updated_at':now,'questions':[{'id':'q',**content}], 'rules':[], 'question_sources':[source]})
        if i<100:scopes.append({'survey_id':sid,'survey_version':1,'instance_id':'q','version_id':vid})
        for j in range(5):
            key=f'{sid}-{j}';responses.append({'_id':key,'schema_version':1,'survey_id':sid,'survey_version':1,
                'respondent_key':key,'attempt_key':key,'submission_key':key,'anonymous':True,'user_id':None,
                'answers':{'q':key},'path':['q'],'submitted_at':now+timedelta(seconds=j)})
    db.surveys.insert_many(surveys);db.responses.insert_many(responses)
    captures={};original=Collection.aggregate
    def capture(self,pipeline,*a,**kw):
        captures['pipeline']=deepcopy(pipeline);return original(self,pipeline,*a,**kw)
    Collection.aggregate=capture
    try:
        LibraryPages(db).usages(tid,vid,owner,1,20);usage=captures['pipeline']
        LibraryStatisticsRepository(db).summarize(owner,scopes,[{'version':versions[0],'question':{'id':'q',**content}}],{'id':'q',**content},5)
        statistics=captures['pipeline']
    finally:Collection.aggregate=original
    queries={
        'library_page':{'find':'question_templates','filter':{'owner_id':owner},'sort':{'updated_at':-1,'_id':1},'skip':2000,'limit':20},
        'source_version_references':{'aggregate':'surveys','pipeline':usage,'cursor':{}},
        'cross_survey_statistics_page5':{'aggregate':'surveys','pipeline':statistics,'cursor':{}},
        'history_deep_cursor':{'find':'question_versions','filter':{'template_id':tid,'number':{'$lt':1000}},'sort':{'number':-1},'limit':20},
        'history_full_legacy':{'find':'question_versions','filter':{'template_id':tid},'sort':{'number':-1},'limit':5000}}
    def measure():
        result={}
        for key,command in queries.items():
            explain=db.command({'explain':command,'verbosity':'executionStats'})
            timings=[]
            for _ in range(7):
                start=perf_counter()
                if 'find' in command:
                    list(db[command['find']].find(command['filter']).sort(list(command['sort'].items())).skip(command.get('skip',0)).limit(command['limit']))
                else:list(db[command['aggregate']].aggregate(command['pipeline']))
                timings.append((perf_counter()-start)*1000)
            result[key]={'median_ms':median(timings),'samples_ms':timings,'explain':explain}
        return result
    before=measure();initialize_banks(db);after=measure()
    output={'database':db.name,'counts':{'templates':3000,'versions':5000,'surveys':2000,'responses':10000},
            'runs':7,'note':'Same retained data, baseline warmup precedes after; local timings are not load/production benchmarks.',
            'before':before,'after':after}
    path=Path('.runtime/phase2-pages/query-benchmark.json');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'database':db.name,'before_ms':{k:v['median_ms'] for k,v in before.items()},
                      'after_ms':{k:v['median_ms'] for k,v in after.items()}},ensure_ascii=False))


if __name__=='__main__':main()
