/** @typedef {{id:number,code:string,name:string,starts_at:string,ends_at:string,a_ratio:number,hypothesis:string,layer_id:number|null,status:'active'|'stopped'}} Experiment */
/** @typedef {{id:number,code:string,name:string,description:string,salt:string}} Layer */
/** @typedef {{id:number,layer_id:number,version:number,status:'draft'|'active'|'archived',allocations:{experiment_id:number,ratio:number}[],note:string,created_by:string,created_at:string,activated_by:string,activated_at:string,archived_at:string}} TrafficRule */
/** @typedef {{id:number,layer_id:number,experiment_id:number,user_key:string,group:'A'|'B',rule_version:number,status:'active'|'released',experiment_code:string,assigned_at:string,released_at:string,release_reason:string}} Assignment */
/** @typedef {{id:number,experiment_id:number,user_key:string,group:'A'|'B',kind:'exposure'|'activation',occurred_at:string,assignment_id:number|null,late:number}} TrialEvent */
/** @typedef {{id:number,experiment_id:number,state:string,result:object,snapshot:object,rationale:string,action:string,published_at:string}} Decision */
export const CONTRACT_VERSION='2.0';
